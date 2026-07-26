"""Tests for on-disk reclaim. The real vault had 1.2 GB of index for 1.8 MB
of vectors, almost all of it orphaned segments and un-vacuumed sqlite pages."""

from pathlib import Path

import pytest

from kyp_mem import maintenance as mt
from kyp_mem.vault import Vault
from tests.conftest import write

pytest.importorskip("chromadb")

pytestmark = pytest.mark.slow

ORPHAN_ID = "deadbeef-0000-4000-8000-000000000000"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    write(root, "P/Knowledge.md", "# Knowledge\n\n## Bugs\n\nA race in the stop hook.\n\n## Notes\n\nThe vault is the source of truth.\n")
    write(root, "P/Sessions/s1.md", "# Session\n\n## LEARNED\n\nChroma never reclaims tombstoned slots.\n")
    v = Vault(str(root))
    v.ensure_vector_synced()
    return v


def test_sibling_vaults_get_separate_index_dirs(tmp_path):
    """The bug that caused unbounded growth in the wild.

    Two vaults in one folder previously resolved to the same index directory.
    Each sync then treated the other's notes as deleted, pruned them, and
    re-embedded its own — so alternating between vaults wiped and rebuilt the
    whole index every time.
    """
    from kyp_mem.vector import index_dir_for

    a = tmp_path / "docs" / "memory"
    b = tmp_path / "docs" / "project-docs"
    assert index_dir_for(a) != index_dir_for(b)
    assert index_dir_for(a) == index_dir_for(a), "must be stable across calls"


def test_legacy_shared_index_is_detected_but_not_auto_deleted(tmp_path):
    from kyp_mem.vector import VectorStore

    vault = tmp_path / "docs" / "memory"
    vault.mkdir(parents=True)
    legacy = tmp_path / "docs" / "chroma"
    legacy.mkdir()
    (legacy / "chroma.sqlite3").write_bytes(b"x" * 20_000)

    store = VectorStore(str(vault))
    report = mt.legacy_index_report(store)
    assert report["present"] is True
    assert report["bytes"] >= 20_000
    assert legacy.exists(), "detection alone must never delete"

    assert mt.remove_legacy_index(store, dry_run=True)["removed"] is True
    assert legacy.exists(), "dry run must not delete"

    assert mt.remove_legacy_index(store)["removed"] is True
    assert not legacy.exists()


def test_no_legacy_index_reported_when_absent(tmp_path):
    from kyp_mem.vector import VectorStore

    vault = tmp_path / "docs" / "memory"
    vault.mkdir(parents=True)
    assert mt.legacy_index_report(VectorStore(str(vault)))["present"] is False


def test_human_bytes():
    assert mt.human_bytes(512) == "512 B"
    assert mt.human_bytes(1536) == "1.5 KB"
    assert mt.human_bytes(1024 ** 3) == "1.0 GB"


def test_inspect_reports_a_missing_store(tmp_path):
    report = mt.inspect(tmp_path / "nope")
    assert report["exists"] is False
    assert report["total_bytes"] == 0


def test_inspect_finds_live_segments(vault):
    report = mt.inspect(vault.vector.db_path)
    assert report["exists"] is True
    assert report["segments"]
    assert report["orphans"] == []
    assert report["embeddings"] > 0


def test_orphan_segment_is_detected_and_removed(vault):
    chroma_dir = vault.vector.db_path
    orphan = chroma_dir / ORPHAN_ID
    orphan.mkdir()
    (orphan / "data_level0.bin").write_bytes(b"x" * 50_000)

    report = mt.inspect(chroma_dir)
    assert [o["id"] for o in report["orphans"]] == [ORPHAN_ID]

    result = mt.remove_orphan_segments(chroma_dir)
    assert result["removed"] == [ORPHAN_ID]
    assert result["freed_bytes"] >= 50_000
    assert not orphan.exists()


def test_dry_run_removes_nothing(vault):
    chroma_dir = vault.vector.db_path
    orphan = chroma_dir / ORPHAN_ID
    orphan.mkdir()
    (orphan / "blob.bin").write_bytes(b"y" * 10_000)

    result = mt.remove_orphan_segments(chroma_dir, dry_run=True)
    assert result["removed"] == [ORPHAN_ID]
    assert orphan.exists(), "dry run must not delete anything"


def test_live_segments_are_never_removed(vault):
    chroma_dir = vault.vector.db_path
    before = {s["id"] for s in mt.inspect(chroma_dir)["segments"]}
    mt.remove_orphan_segments(chroma_dir)
    after = {s["id"] for s in mt.inspect(chroma_dir)["segments"]}
    assert before == after


def test_non_uuid_directories_are_left_alone(vault):
    chroma_dir = vault.vector.db_path
    keep = chroma_dir / "my-backup"
    keep.mkdir()
    (keep / "f.txt").write_text("important")
    mt.remove_orphan_segments(chroma_dir)
    assert keep.exists()


def test_drop_unused_collections(vault):
    store = vault.vector
    store.collection  # force connect
    store._client.get_or_create_collection(name="legacy-sessions")

    names = {n for _, n in mt._live_collections(store.db_path)}
    assert "legacy-sessions" in names

    result = mt.drop_unused_collections(store.db_path, keep_names={store.collection_name})
    assert "legacy-sessions" in result["dropped"]

    names = {n for _, n in mt._live_collections(store.db_path)}
    assert "legacy-sessions" not in names
    assert store.collection_name in names


def test_optimize_fulltext_merges_fts_segments(vault):
    store = vault.vector
    # Churn the index so FTS5 accumulates segments, which it never merges on
    # its own. This is what grew to 62.9 MB for 184 embeddings on a real vault.
    for i in range(12):
        store.upsert_note(f"P/Churn.md", "P", "Churn", f"# Churn\n\nrevision {i} " + "token " * 60, [])

    before = mt.inspect(store.db_path)["sqlite_bytes"]
    result = mt.optimize_fulltext(store.db_path)
    assert result["optimized"], "expected at least one FTS table to optimize"
    mt.vacuum(store.db_path)
    after = mt.inspect(store.db_path)["sqlite_bytes"]
    assert after <= before

    # Optimizing must not damage the index.
    store.close()
    assert vault.search_sessions("tombstoned slots", min_similarity=0.0) is not None
    assert store.search("revision", min_similarity=0.0)


def test_optimize_fulltext_on_missing_store_is_safe(tmp_path):
    assert mt.optimize_fulltext(tmp_path / "nope")["optimized"] == []


def test_vacuum_shrinks_or_holds_the_sqlite_file(vault):
    result = mt.vacuum(vault.vector.db_path)
    assert result["after_bytes"] <= result["before_bytes"]
    assert result["after_bytes"] > 0


def test_vacuum_on_missing_store_is_safe(tmp_path):
    assert mt.vacuum(tmp_path / "nope")["freed_bytes"] == 0


def test_compact_reclaims_orphans_and_keeps_search_working(vault):
    chroma_dir = vault.vector.db_path
    orphan = chroma_dir / ORPHAN_ID
    orphan.mkdir()
    (orphan / "data_level0.bin").write_bytes(b"z" * 200_000)

    result = mt.compact(vault)
    assert result["ok"] is True
    assert not orphan.exists()
    assert result["freed_bytes"] > 0

    # The whole point: reclaiming disk must not lose knowledge.
    hits = vault.search_sessions("tombstoned slots reclaimed")
    assert hits, "semantic index must still answer after a compact"


def test_compact_reported_totals_are_coherent_when_purging_legacy(vault):
    """Freed can never exceed the starting total.

    The legacy directory has to be counted in `before` when it is going to be
    removed, or the reported percentage goes over 100.
    """
    legacy = Path(vault.vector.legacy_db_path)
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "chroma.sqlite3").write_bytes(b"x" * 400_000)

    result = mt.compact(vault, purge_legacy=True)
    assert result["freed_bytes"] <= result["before_bytes"]
    assert result["after_bytes"] <= result["before_bytes"]
    assert result["before_bytes"] - result["after_bytes"] == result["freed_bytes"]
    assert not legacy.exists()


def test_compact_totals_exclude_legacy_when_not_purging(vault):
    legacy = Path(vault.vector.legacy_db_path)
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "chroma.sqlite3").write_bytes(b"x" * 400_000)

    result = mt.compact(vault, purge_legacy=False)
    assert result["freed_bytes"] <= result["before_bytes"]
    assert legacy.exists(), "must not delete without an explicit purge"


def test_compact_dry_run_changes_nothing(vault):
    chroma_dir = vault.vector.db_path
    orphan = chroma_dir / ORPHAN_ID
    orphan.mkdir()
    (orphan / "b.bin").write_bytes(b"q" * 10_000)

    mt.compact(vault, dry_run=True)
    assert orphan.exists()


def test_compact_rebuilds_index_from_markdown(vault):
    store = vault.vector
    before = store.stats()["documents"]
    mt.compact(vault)
    assert vault.vector.stats()["documents"] == before


def test_compact_without_vector_reports_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("KYP_NO_VECTOR", "1")
    root = tmp_path / "v"
    write(root, "a.md", "body")
    v = Vault(str(root))
    result = mt.compact(v)
    assert result["ok"] is False
