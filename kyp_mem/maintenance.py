"""Vector-store maintenance: orphan removal, compaction, reindexing.

Chroma never gives back disk on its own. Deleting a record only tombstones a
slot in the HNSW graph, and dropping a collection leaves its segment directory
behind. On a real vault that had been running for two months this produced
1.2 GB of on-disk index for 1.8 MB of actual vectors, with the HNSW graph
holding ~236,000 allocated slots for 1,191 live records.

Nothing here touches the markdown vault, which is the source of truth. The
worst case for every operation in this module is that the index is rebuilt
from the notes.
"""

import shutil
import sqlite3
from pathlib import Path

UUID_LEN = 36


def _dir_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def _looks_like_uuid(name: str) -> bool:
    return len(name) == UUID_LEN and name.count("-") == 4


def _sqlite_path(chroma_dir) -> Path:
    """Every entry point here takes the chroma *directory*; resolve the db file."""
    return Path(chroma_dir) / "chroma.sqlite3"


def _query_readonly(chroma_dir, sql: str, default):
    path = _sqlite_path(chroma_dir)
    if not path.exists():
        return default
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return default
    try:
        return list(con.execute(sql))
    except sqlite3.Error:
        return default
    finally:
        con.close()


def _live_segment_ids(chroma_dir) -> set:
    """Segment UUIDs Chroma still references."""
    return {row[0] for row in _query_readonly(chroma_dir, "SELECT id FROM segments", [])}


def _live_collections(chroma_dir) -> list:
    return [(r[0], r[1]) for r in _query_readonly(chroma_dir, "SELECT id, name FROM collections", [])]


def inspect(chroma_dir) -> dict:
    """Report on-disk state without changing anything."""
    chroma_dir = Path(chroma_dir)
    sqlite_path = _sqlite_path(chroma_dir)
    report = {
        "path": str(chroma_dir),
        "exists": chroma_dir.exists(),
        "total_bytes": 0,
        "sqlite_bytes": 0,
        "reclaimable_sqlite_bytes": 0,
        "segments": [],
        "orphans": [],
        "collections": [],
        "embeddings": 0,
    }
    if not chroma_dir.exists():
        return report

    report["total_bytes"] = _dir_size(chroma_dir)
    if sqlite_path.exists():
        report["sqlite_bytes"] = sqlite_path.stat().st_size
        con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        try:
            page_size = con.execute("PRAGMA page_size").fetchone()[0]
            freelist = con.execute("PRAGMA freelist_count").fetchone()[0]
            report["reclaimable_sqlite_bytes"] = page_size * freelist
            report["embeddings"] = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        except sqlite3.Error:
            pass
        finally:
            con.close()

    live = _live_segment_ids(chroma_dir)
    report["collections"] = _live_collections(chroma_dir)

    for child in chroma_dir.iterdir():
        if not child.is_dir() or not _looks_like_uuid(child.name):
            continue
        entry = {"id": child.name, "bytes": _dir_size(child)}
        report["segments"].append(entry)
        if child.name not in live:
            report["orphans"].append(entry)

    return report


def remove_orphan_segments(chroma_dir, dry_run: bool = False) -> dict:
    """Delete segment directories Chroma no longer references.

    These are left behind when a collection is dropped or rebuilt. They are
    never read again, but they are typically the largest thing on disk.
    """
    chroma_dir = Path(chroma_dir)
    if not _sqlite_path(chroma_dir).exists():
        return {"removed": [], "freed_bytes": 0}

    live = _live_segment_ids(chroma_dir)
    removed, freed = [], 0
    for child in sorted(chroma_dir.iterdir()):
        if not child.is_dir() or not _looks_like_uuid(child.name):
            continue
        if child.name in live:
            continue
        size = _dir_size(child)
        if not dry_run:
            shutil.rmtree(child, ignore_errors=True)
        removed.append(child.name)
        freed += size
    return {"removed": removed, "freed_bytes": freed}


def drop_unused_collections(chroma_dir, keep_names, dry_run: bool = False) -> dict:
    """Delete Chroma collections that are not in ``keep_names``.

    Catches collections left by earlier schema versions — for example the
    v1 ``sessions`` collection, which indexed only session notes.
    """
    chroma_dir = Path(chroma_dir)
    if not _sqlite_path(chroma_dir).exists():
        return {"dropped": []}

    keep = set(keep_names)
    stale = [(cid, name) for cid, name in _live_collections(chroma_dir) if name not in keep]
    if not stale or dry_run:
        return {"dropped": [name for _, name in stale]}

    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_dir))
    dropped = []
    for _, name in stale:
        try:
            client.delete_collection(name=name)
            dropped.append(name)
        except Exception:
            pass
    return {"dropped": dropped}


def optimize_fulltext(chroma_dir) -> dict:
    """Merge Chroma's FTS5 index segments.

    FTS5 appends a new b-tree segment on every insert and delete and never
    merges them on its own; VACUUM does not touch them either, because to
    sqlite they are just ordinary table rows. On a store that saw heavy
    write churn this dominates everything else on disk — 62.9 MB of segments
    for 184 live embeddings on the vault this was written against.

    ``optimize`` merges them into one segment. Must run before VACUUM so the
    pages it frees actually get returned.
    """
    sqlite_path = _sqlite_path(chroma_dir)
    if not sqlite_path.exists():
        return {"optimized": [], "freed_bytes": 0}

    tables = [
        r[0]
        for r in _query_readonly(
            chroma_dir,
            "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%USING fts%'",
            [],
        )
    ]
    if not tables:
        return {"optimized": [], "freed_bytes": 0}

    before = sqlite_path.stat().st_size
    optimized = []
    con = sqlite3.connect(str(sqlite_path))
    try:
        for table in tables:
            try:
                con.execute(f'INSERT INTO "{table}"("{table}") VALUES(\'optimize\')')
                con.commit()
                optimized.append(table)
            except sqlite3.Error:
                pass
    finally:
        con.close()
    # Space shows up only after VACUUM; report the pre-vacuum delta as 0.
    return {"optimized": optimized, "freed_bytes": max(0, before - sqlite_path.stat().st_size)}


def vacuum(chroma_dir) -> dict:
    """Compact the sqlite file, returning freed pages to the filesystem."""
    sqlite_path = _sqlite_path(chroma_dir)
    if not sqlite_path.exists():
        return {"before_bytes": 0, "after_bytes": 0, "freed_bytes": 0}

    before = sqlite_path.stat().st_size
    con = sqlite3.connect(str(sqlite_path))
    try:
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()
    after = sqlite_path.stat().st_size
    return {"before_bytes": before, "after_bytes": after, "freed_bytes": before - after}


def legacy_index_report(store) -> dict:
    """Detect a shared pre-rename index directory.

    Before index directories were made unique per vault, every vault under the
    same parent shared one store and repeatedly pruned each other's notes. Any
    leftover directory is now dead weight, but it may still be in use by an
    older kyp-mem install, so removal is always explicit.
    """
    legacy = Path(store.legacy_db_path)
    if not legacy.exists() or legacy.resolve() == Path(store.db_path).resolve():
        return {"present": False, "bytes": 0, "path": str(legacy)}
    return {"present": True, "bytes": _dir_size(legacy), "path": str(legacy)}


def remove_legacy_index(store, dry_run: bool = False) -> dict:
    report = legacy_index_report(store)
    if not report["present"]:
        return {"removed": False, "freed_bytes": 0}
    if not dry_run:
        shutil.rmtree(report["path"], ignore_errors=True)
    return {"removed": True, "freed_bytes": report["bytes"], "path": report["path"]}


def compact(vault, rebuild: bool = True, dry_run: bool = False, purge_legacy: bool = False) -> dict:
    """Full reclaim pass.

    With ``rebuild=True`` the semantic index is dropped and rebuilt from the
    markdown notes. That is the only way to reclaim tombstoned HNSW slots —
    Chroma has no in-place compaction for them — and it is safe because the
    notes are the source of truth.
    """
    store = vault.vector
    if store is None:
        return {"ok": False, "reason": "vector index disabled"}

    chroma_dir = store.db_path
    before = inspect(chroma_dir)
    steps = {}

    if rebuild and not dry_run:
        store.rebuild()
        steps["sync"] = vault.sync_vector()
        vault._vector_ready = True

    steps["collections"] = drop_unused_collections(
        chroma_dir, keep_names={store.collection_name}, dry_run=dry_run
    )
    # Dropping collections orphans their segment dirs, so sweep afterwards.
    store.close()
    steps["orphans"] = remove_orphan_segments(chroma_dir, dry_run=dry_run)
    if not dry_run:
        # Merge FTS5 segments before vacuuming, or the pages they occupy stay
        # allocated and VACUUM has nothing to reclaim.
        steps["fulltext"] = optimize_fulltext(chroma_dir)
        steps["vacuum"] = vacuum(chroma_dir)

    steps["legacy"] = legacy_index_report(store)
    legacy_freed = 0
    if purge_legacy:
        removed = remove_legacy_index(store, dry_run=dry_run)
        steps["legacy_removed"] = removed
        legacy_freed = removed.get("freed_bytes", 0)

    after = inspect(chroma_dir)
    return {
        "ok": True,
        "dry_run": dry_run,
        "before_bytes": before["total_bytes"],
        "after_bytes": after["total_bytes"],
        "freed_bytes": before["total_bytes"] - after["total_bytes"] + legacy_freed,
        "steps": steps,
        "report": after,
    }
