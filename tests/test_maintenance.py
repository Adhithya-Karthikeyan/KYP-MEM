"""Tests for on-disk reclaim.

The SQLite backend has much less to reclaim than Chroma did (DELETE frees
pages, VACUUM returns them), so these tests focus on what remains: vacuum,
sweeping the old ChromaDB files a pre-1.2 install left behind, and the
strictly-guarded removal of the pre-1.0 shared index directory.
"""

from pathlib import Path

import pytest

from kyp_mem import maintenance as mt
from kyp_mem.vault import Vault
from tests.conftest import write

pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

pytestmark = pytest.mark.slow

# A UUID-named directory, as Chroma's segment dirs were.
CHROMA_SEGMENT_ID = "deadbeef-0000-4000-8000-000000000000"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    write(root, "P/Knowledge.md", "# Knowledge\n\n## Bugs\n\nA race in the stop hook.\n\n## Notes\n\nThe vault is the source of truth.\n")
    write(root, "P/Sessions/s1.md", "# Session\n\n## LEARNED\n\nChroma never reclaims tombstoned slots.\n")
    v = Vault(str(root))
    v.ensure_vector_synced()
    return v


def _plant_chroma_leftovers(index_dir: Path, nbytes: int = 50_000):
    (index_dir / "chroma.sqlite3").write_bytes(b"x" * nbytes)
    segment = index_dir / CHROMA_SEGMENT_ID
    segment.mkdir()
    (segment / "data_level0.bin").write_bytes(b"x" * nbytes)
    return segment


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


def test_inspect_reports_live_contents(vault):
    report = mt.inspect(vault.vector.db_path)
    assert report["exists"] is True
    assert report["chunks"] > 0
    assert report["documents"] == 2
    assert report["db_bytes"] > 0
    assert report["embedder"], "the embedder spec must be persisted"
    assert report["chroma_leftovers"] == []


def test_integrity_check_passes_on_a_good_store(vault):
    ok, detail = mt.check_integrity(vault.vector.db_path)
    assert ok, detail


def test_inspect_handles_uri_hostile_paths(tmp_path):
    """'#' and '?' in the vault path must not break read-only inspection.

    SQLite's URI parser treats '?' as the query string and drops everything
    after '#' as a fragment, so an unencoded path made inspect open the wrong
    file and report a healthy index as empty.
    """
    from kyp_mem.vector import VectorStore

    root = tmp_path / "my vault #1?x"
    root.mkdir(parents=True)
    store = VectorStore(str(root))
    assert store.upsert_note("P/A.md", "P", "A", "# A\n\nhello uri world\n", [])

    report = mt.inspect(store.db_path)
    assert report["chunks"] > 0, "inspect must see the same file the store writes"
    ok, detail = mt.check_integrity(store.db_path)
    assert ok, detail


def test_integrity_check_fails_on_garbage(tmp_path):
    index_dir = tmp_path / "idx"
    index_dir.mkdir()
    (index_dir / mt.DB_FILENAME).write_bytes(b"this is not a sqlite database")
    ok, _ = mt.check_integrity(index_dir)
    assert ok is False


def test_chroma_leftovers_are_detected_and_removed(vault):
    index_dir = vault.vector.db_path
    segment = _plant_chroma_leftovers(index_dir)

    report = mt.inspect(index_dir)
    assert "chroma.sqlite3" in report["chroma_leftovers"]
    assert CHROMA_SEGMENT_ID in report["chroma_leftovers"]
    assert report["chroma_leftover_bytes"] >= 100_000

    result = mt.remove_chroma_leftovers(index_dir)
    assert set(result["removed"]) == {"chroma.sqlite3", CHROMA_SEGMENT_ID}
    assert result["freed_bytes"] >= 100_000
    assert not segment.exists()
    assert not (index_dir / "chroma.sqlite3").exists()


def test_chroma_sweep_dry_run_removes_nothing(vault):
    index_dir = vault.vector.db_path
    segment = _plant_chroma_leftovers(index_dir)

    result = mt.remove_chroma_leftovers(index_dir, dry_run=True)
    assert set(result["removed"]) == {"chroma.sqlite3", CHROMA_SEGMENT_ID}
    assert segment.exists(), "dry run must not delete anything"
    assert (index_dir / "chroma.sqlite3").exists()


def test_chroma_sweep_never_touches_the_live_index_or_other_files(vault):
    index_dir = vault.vector.db_path
    keep = index_dir / "my-backup"
    keep.mkdir()
    (keep / "f.txt").write_text("important")

    mt.remove_chroma_leftovers(index_dir)
    assert keep.exists()
    assert (index_dir / mt.DB_FILENAME).exists()
    assert vault.vector.stats()["chunks"] > 0


def test_vacuum_reclaims_after_heavy_deletion(vault):
    store = vault.vector
    # Churn: add a bulky note, then delete it. Pages go to the freelist and
    # only VACUUM hands them back to the filesystem.
    store.upsert_note("P/Churn.md", "P", "Churn", "# Churn\n\n" + "token " * 20_000, [])
    store.delete_note("P/Churn.md")

    result = mt.vacuum(store.db_path)
    assert result["freed_bytes"] > 0, "vacuum after heavy churn must actually return disk"
    assert result["after_bytes"] > 0


def test_vacuum_on_missing_store_is_safe(tmp_path):
    assert mt.vacuum(tmp_path / "nope")["freed_bytes"] == 0


def test_compact_reclaims_chroma_files_and_keeps_search_working(vault):
    index_dir = vault.vector.db_path
    segment = _plant_chroma_leftovers(index_dir, nbytes=200_000)

    result = mt.compact(vault)
    assert result["ok"] is True
    assert not segment.exists()
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
    index_dir = vault.vector.db_path
    segment = _plant_chroma_leftovers(index_dir)

    mt.compact(vault, dry_run=True)
    assert segment.exists()
    assert (index_dir / "chroma.sqlite3").exists()


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
