"""Semantic-index maintenance: compaction, integrity, legacy cleanup.

The SQLite backend needs far less of this than the ChromaDB one did — DELETE
actually frees pages and VACUUM returns them to the filesystem, so there are
no tombstoned HNSW slots and no orphaned segment directories to hunt. What
remains:

  - VACUUM after churn, so the file shrinks back to its contents.
  - Sweeping the ChromaDB files a pre-1.2 install left in the index directory
    (chroma.sqlite3 plus UUID-named segment dirs) — often hundreds of MB of
    dead weight for upgrading users.
  - The pre-1.0 *shared* index directory, which predates per-vault index
    paths and is only ever removed explicitly (``--purge-legacy``).

Nothing here touches the markdown vault, which is the source of truth. The
worst case for every operation in this module is that the index is rebuilt
from the notes.
"""

import shutil
import sqlite3
import urllib.parse
from pathlib import Path

from .vector import DB_FILENAME

UUID_LEN = 36


def _ro_connect(db_file: Path) -> sqlite3.Connection:
    """Read-only connection via a file: URI.

    The path must be percent-encoded: SQLite's URI parser treats ``?`` as the
    start of the query string and drops everything after ``#`` as a fragment,
    so a vault directory containing either character would silently make this
    open the wrong path (measured: inspect reported 0 chunks for a healthy
    index under a directory with ``#`` in its name).
    """
    encoded = urllib.parse.quote(str(db_file))
    return sqlite3.connect(f"file:{encoded}?mode=ro", uri=True)


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


def _db_file(index_dir) -> Path:
    return Path(index_dir) / DB_FILENAME


def chroma_leftovers(index_dir) -> list:
    """Files the old ChromaDB backend left in the index directory.

    Recognised positively — the chroma database file and UUID-named segment
    directories — rather than "anything we don't know", so a user file that
    strays into the directory is never swept up.
    """
    index_dir = Path(index_dir)
    if not index_dir.is_dir():
        return []
    out = []
    chroma_db = index_dir / "chroma.sqlite3"
    if chroma_db.is_file():
        out.append(chroma_db)
    for child in sorted(index_dir.iterdir()):
        if child.is_dir() and _looks_like_uuid(child.name):
            out.append(child)
    return out


def remove_chroma_leftovers(index_dir, dry_run: bool = False) -> dict:
    """Delete the old backend's files. They are never read again."""
    removed, freed = [], 0
    for item in chroma_leftovers(index_dir):
        size = _dir_size(item) if item.is_dir() else item.stat().st_size
        if not dry_run:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        removed.append(item.name)
        freed += size
    return {"removed": removed, "freed_bytes": freed}


def inspect(index_dir) -> dict:
    """Report on-disk state without changing anything."""
    index_dir = Path(index_dir)
    db_file = _db_file(index_dir)
    report = {
        "path": str(index_dir),
        "exists": index_dir.exists(),
        "total_bytes": 0,
        "db_bytes": 0,
        "reclaimable_bytes": 0,
        "chunks": 0,
        "documents": 0,
        "embedder": "",
        "chroma_leftover_bytes": 0,
        "chroma_leftovers": [],
        "integrity": None,
    }
    if not index_dir.exists():
        return report

    report["total_bytes"] = _dir_size(index_dir)

    leftovers = chroma_leftovers(index_dir)
    report["chroma_leftovers"] = [item.name for item in leftovers]
    report["chroma_leftover_bytes"] = sum(
        _dir_size(i) if i.is_dir() else i.stat().st_size for i in leftovers
    )

    if db_file.exists():
        report["db_bytes"] = db_file.stat().st_size
        try:
            con = _ro_connect(db_file)
        except sqlite3.Error:
            return report
        try:
            page_size = con.execute("PRAGMA page_size").fetchone()[0]
            freelist = con.execute("PRAGMA freelist_count").fetchone()[0]
            report["reclaimable_bytes"] = page_size * freelist
            report["chunks"] = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            report["documents"] = con.execute(
                "SELECT COUNT(DISTINCT doc_path) FROM chunks"
            ).fetchone()[0]
            row = con.execute("SELECT value FROM meta WHERE key='embedder'").fetchone()
            report["embedder"] = row[0] if row else ""
        except sqlite3.Error:
            pass
        finally:
            con.close()

    return report


def check_integrity(index_dir) -> tuple:
    """SQLite's own integrity check — cheap and catches real corruption."""
    db_file = _db_file(index_dir)
    if not db_file.exists():
        return True, "no database"
    try:
        con = _ro_connect(db_file)
    except sqlite3.Error as e:
        return False, repr(e)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok", result
    except sqlite3.Error as e:
        return False, repr(e)
    finally:
        con.close()


def vacuum(index_dir) -> dict:
    """Compact the database, returning freed pages to the filesystem.

    Also truncates the WAL: a crashed reader can leave a large -wal file
    behind, and VACUUM alone does not shrink it.
    """
    db_file = _db_file(index_dir)
    if not db_file.exists():
        return {"before_bytes": 0, "after_bytes": 0, "freed_bytes": 0}

    def _size():
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_file) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    before = _size()
    con = sqlite3.connect(str(db_file))
    try:
        # Errors propagate: a VACUUM that cannot run means something is wrong
        # with the store, and reporting "0 bytes freed" as if it succeeded
        # would hide that from both the user and the tests.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()
    after = _size()
    return {"before_bytes": before, "after_bytes": after, "freed_bytes": before - after}


def legacy_index_report(store) -> dict:
    """Detect the pre-1.0 shared index directory.

    Before index directories were made unique per vault, every vault under the
    same parent shared one store and repeatedly pruned each other's notes. Any
    leftover directory is now dead weight, but it may still be in use by an
    older kyp-mem install, so removal is always explicit.

    The legacy path is derived purely from a *name* (``<vault>/../chroma``),
    and the caller hands the result to ``rmtree``. So this must prove the
    directory really is a Chroma store before reporting it. A vault that
    happens to be named ``chroma``, or that contains notes under a ``chroma``
    folder, would otherwise be deleted along with every note in it.
    """
    absent = {"present": False, "bytes": 0, "path": str(store.legacy_db_path)}
    try:
        # A symlink at the legacy location is refused outright: resolve()
        # would follow it and the eventual rmtree would destroy the *target*,
        # somewhere we never vetted. A genuine pre-1.0 index is a real
        # directory; anything else is not ours to delete.
        if Path(store.legacy_db_path).is_symlink():
            return absent
        legacy = Path(store.legacy_db_path).resolve()
        vault = Path(store.vault_path).resolve()
        active = Path(store.db_path).resolve()
    except OSError:
        return absent

    if not legacy.is_dir() or legacy in (active, vault):
        return absent
    # Never touch anything containing, contained by, or equal to the vault.
    if vault in legacy.parents or legacy in vault.parents:
        return absent
    # Positive proof it is a Chroma store, and negative proof it holds no notes.
    if not (legacy / "chroma.sqlite3").is_file():
        return absent
    if any(legacy.rglob("*.md")):
        return absent

    return {"present": True, "bytes": _dir_size(legacy), "path": str(legacy)}


def remove_legacy_index(store, dry_run: bool = False) -> dict:
    report = legacy_index_report(store)
    if not report["present"]:
        return {"removed": False, "freed_bytes": 0}
    if not dry_run:
        shutil.rmtree(report["path"], ignore_errors=True)
    return {"removed": True, "freed_bytes": report["bytes"], "path": report["path"]}


def compact(vault, rebuild: bool = True, dry_run: bool = False, purge_legacy: bool = False) -> dict:
    """Full reclaim pass: optional re-embed, chroma sweep, vacuum, legacy purge.

    Rebuilding is no longer *required* to reclaim space the way it was under
    Chroma, but it remains the recovery path for a drifted or corrupt index,
    and it is safe because the notes are the source of truth.
    """
    store = vault.vector
    if store is None:
        return {"ok": False, "reason": "vector index disabled"}

    index_dir = store.db_path
    before = inspect(index_dir)
    # The legacy directory counts toward the starting total only when we are
    # actually going to remove it. Otherwise "freed" could exceed "before" and
    # the reported percentage would go over 100.
    legacy_before = legacy_index_report(store)["bytes"] if purge_legacy else 0
    steps = {}

    if rebuild and not dry_run:
        # Prove the embedding model loads BEFORE dropping the vectors. An
        # offline machine (or a base install) would otherwise wipe a healthy
        # index, fail the re-embed, and report success — the exact opposite
        # of maintenance. The loaded embedder is cached on the store, so the
        # sync that follows reuses it rather than paying twice.
        from .embedder import EmbedderUnavailable

        try:
            store._get_embedder()
        except EmbedderUnavailable as e:
            steps["sync"] = {"ok": False, "reason": str(e)}
        else:
            store.rebuild()
            steps["sync"] = vault.sync_vector()
            vault._vector_ready = True

    steps["chroma"] = remove_chroma_leftovers(index_dir, dry_run=dry_run)
    if not dry_run:
        steps["vacuum"] = vacuum(index_dir)

    steps["legacy"] = legacy_index_report(store)
    legacy_freed = 0
    if purge_legacy:
        removed = remove_legacy_index(store, dry_run=dry_run)
        steps["legacy_removed"] = removed
        legacy_freed = removed.get("freed_bytes", 0)

    after = inspect(index_dir)
    total_before = before["total_bytes"] + legacy_before
    total_after = after["total_bytes"] + (legacy_before - legacy_freed)
    return {
        "ok": True,
        "dry_run": dry_run,
        "before_bytes": total_before,
        "after_bytes": total_after,
        "freed_bytes": total_before - total_after,
        "steps": steps,
        "report": after,
    }
