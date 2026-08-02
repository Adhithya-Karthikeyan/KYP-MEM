"""Integration tests against a real vector store in a temp directory."""

import pytest

from kyp_mem.vector import VectorStore, content_hash

pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

pytestmark = pytest.mark.slow


KNOWLEDGE_MD = """# Knowledge

## Architecture

The MCP server exposes vault tools over stdio and is started by Claude Code.

## Bugs

The stop hook raced on a shared activity log file, so two concurrent sessions
summarised each other's work into the wrong project note.

## Key Decisions

Markdown on disk stays the source of truth; every derived index is disposable
and can be rebuilt from the notes at any time.
"""

SESSION_MD = """# Session s1

## Summary

Investigated runaway disk usage in the persistence layer.

## LEARNED

Chroma tombstones deleted vectors and never reclaims the disk they used, so the
index kept growing long after the records were gone.

## COMPLETED

Added an orphan-segment sweep and a vacuum step to the maintenance command.
"""

GREENLEAF_MD = """# GreenLeaf

## Overview

A gardening companion app that tracks watering schedules for houseplants.

## Features

Species lookup, soil moisture reminders, and a photo journal of plant growth.
"""

DOCS = {
    "KYP-MEM/Knowledge.md": ("KYP-MEM", "Knowledge", KNOWLEDGE_MD, ["knowledge"]),
    "KYP-MEM/Sessions/s1.md": ("KYP-MEM", "Session s1", SESSION_MD, ["session"]),
    "GreenLeaf/Knowledge.md": ("GreenLeaf", "GreenLeaf", GREENLEAF_MD, ["knowledge"]),
}


@pytest.fixture
def store(tmp_path):
    s = VectorStore(str(tmp_path / "vault"))
    yield s
    s.close()


@pytest.fixture
def synced(store):
    store.sync(DOCS)
    return store


def test_construction_opens_nothing(tmp_path):
    # The whole point of the rewrite: building the store must not touch Chroma,
    # so a session-start hook never loads the index.
    s = VectorStore(str(tmp_path / "vault"))
    assert s.is_connected() is False


def test_sync_reports_what_it_did(store):
    summary = store.sync(DOCS)
    assert summary["ok"] is True
    assert summary["added"] == 3
    assert summary["removed"] == 0


def test_indexes_non_session_notes(synced):
    # The old store only embedded Sessions/, so Knowledge.md was unreachable.
    hits = synced.search("gardening houseplants watering")
    assert any(h.doc_path == "GreenLeaf/Knowledge.md" for h in hits)


def test_semantic_paraphrase_match(synced):
    # No shared keywords with the note; only meaning connects them.
    hits = synced.search("why does disk usage keep growing after deletions")
    assert hits
    assert hits[0].doc_path == "KYP-MEM/Sessions/s1.md"


def test_hits_carry_chunk_context(synced):
    hits = synced.search("chroma tombstones")
    assert hits[0].heading.endswith("LEARNED")
    assert hits[0].project == "KYP-MEM"
    assert hits[0].title == "Session s1"


def test_project_filter(synced):
    # "watering plants" also has to *not* pull in the KYP-MEM notes.
    hits = synced.search("watering plants", project="GreenLeaf")
    assert hits
    assert all(h.project == "GreenLeaf" for h in hits)


def test_project_filter_excludes_other_projects(synced):
    hits = synced.search("index disk usage growing", project="GreenLeaf", min_similarity=0.0)
    assert all(h.doc_path.startswith("GreenLeaf/") for h in hits)


def test_sessions_only_filter(synced):
    hits = synced.search("stop hook log", sessions_only=True)
    assert all("/Sessions/" in h.doc_path for h in hits)


def test_similarity_floor_suppresses_junk(synced):
    # An unrelated query must be allowed to return nothing at all — the old
    # store always handed back its top 5 regardless of quality.
    assert synced.search("thai green curry recipe", min_similarity=0.9) == []


def test_similarities_are_descending_and_bounded(synced):
    hits = synced.search("session log", min_similarity=0.0)
    sims = [h.similarity for h in hits]
    assert sims == sorted(sims, reverse=True)
    assert all(-1.01 <= s <= 1.01 for s in sims)


def test_empty_query_returns_nothing(synced):
    assert synced.search("") == []
    assert synced.search("   ") == []


def test_resync_is_idempotent(synced):
    summary = synced.sync(DOCS)
    assert summary["unchanged"] == 3
    assert summary["added"] == 0
    assert summary["updated"] == 0


def test_changed_note_is_reembedded(synced):
    docs = dict(DOCS)
    docs["GreenLeaf/Knowledge.md"] = (
        "GreenLeaf",
        "GreenLeaf",
        "# GreenLeaf\n\nRewritten: now a bicycle repair logbook with torque specifications.\n",
        ["knowledge"],
    )
    summary = synced.sync(docs)
    assert summary["updated"] == 1
    hits = synced.search("bicycle torque specifications")
    assert any(h.doc_path == "GreenLeaf/Knowledge.md" for h in hits)


def test_shrinking_a_note_drops_its_extra_chunks(synced):
    before = synced.stats()["chunks"]
    docs = dict(DOCS)
    docs["KYP-MEM/Knowledge.md"] = ("KYP-MEM", "Knowledge", "# Knowledge\n\nmuch smaller now\n", [])
    synced.sync(docs)
    assert synced.stats()["chunks"] < before
    assert synced.stats()["documents"] == 3


def test_removed_note_is_pruned(synced):
    docs = {k: v for k, v in DOCS.items() if k != "GreenLeaf/Knowledge.md"}
    summary = synced.sync(docs)
    assert summary["removed"] == 1
    assert all(h.doc_path != "GreenLeaf/Knowledge.md" for h in synced.search("gardening", min_similarity=0.0))


def test_prune_actually_reaches_zero_documents(synced):
    synced.sync({})
    assert synced.stats()["documents"] == 0
    assert synced.stats()["chunks"] == 0


def test_upsert_single_note(synced):
    synced.upsert_note(
        "New/Note.md", "New", "Note", "# Note\n\nA distinctive marker about submarine sonar.\n", []
    )
    hits = synced.search("submarine sonar")
    assert hits[0].doc_path == "New/Note.md"


def test_upsert_replaces_rather_than_appends(synced):
    synced.upsert_note("N.md", "P", "N", "# N\n\nfirst version about volcanoes\n", [])
    before = synced.stats()["chunks"]
    synced.upsert_note("N.md", "P", "N", "# N\n\nsecond version about glaciers\n", [])

    # Same shape of note: replacing must not grow the index...
    assert synced.stats()["chunks"] == before
    # ...and no chunk of the first version may survive.
    hits = synced.search("glaciers", min_similarity=0.0, n_results=50)
    n_hits = [h for h in hits if h.doc_path == "N.md"]
    assert n_hits, "N.md must still be indexed after the second upsert"
    assert all("volcano" not in h.text for h in n_hits)


def test_delete_note(synced):
    synced.delete_note("GreenLeaf/Knowledge.md")
    assert all(
        h.doc_path != "GreenLeaf/Knowledge.md"
        for h in synced.search("gardening", min_similarity=0.0)
    )


def test_health_check_passes_on_a_good_store(synced):
    ok, detail = synced.health_check()
    assert ok, detail


def test_rebuild_then_resync_restores_everything(synced):
    synced.rebuild()
    assert synced.stats()["chunks"] == 0
    synced.sync(DOCS)
    assert synced.search("gardening houseplants")


def test_content_hash_is_stable_and_distinguishing():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")


def test_notes_are_split_into_multiple_chunks(synced):
    # One vector per note was the old behaviour and the main accuracy problem.
    assert synced.stats()["chunks"] > synced.stats()["documents"]


# --- model-switch safety ------------------------------------------------------

def _set_stored_embedder(store, spec: str):
    """Simulate another process having re-embedded the index under ``spec``."""
    import sqlite3

    con = sqlite3.connect(str(store.db_file))
    with con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('embedder', ?)", (spec,))
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('generation', "
            "COALESCE((SELECT CAST(value AS INTEGER) + 1 FROM meta WHERE key='generation'), 0))"
        )
    con.close()


def test_stale_process_refuses_to_write_into_a_switched_index(synced):
    """The cross-model poisoning bug: a process holding the old model must not
    insert its vectors into an index re-embedded under a new model — the row
    would carry a matching content hash and survive every future sync."""
    before = synced.stats()["chunks"]
    _set_stored_embedder(synced, "model2vec:someone/other-model")

    ok = synced.upsert_note("P/New.md", "P", "New", "# New\n\npoison attempt\n", [])
    assert ok is False, "write into a foreign-model index must be refused"

    # Nothing was inserted and nothing was rebuilt/clobbered.
    _set_stored_embedder(synced, synced.embedding_profile)
    assert synced.stats()["chunks"] == before


def test_stale_process_search_returns_empty_not_cross_model_scores(synced):
    _set_stored_embedder(synced, "model2vec:someone/other-model")
    # The stored spec disagrees with this process's configured spec (config is
    # isolated to defaults by conftest), so search must refuse, not score.
    assert synced.search("chroma tombstones") == []


def test_process_follows_a_config_backed_model_switch(synced):
    """When the on-disk config agrees with the index's new spec, a running
    process adopts it instead of erroring until restart."""
    from kyp_mem import config
    from kyp_mem.embedder import resolve_spec

    new_spec = resolve_spec("static")
    assert new_spec != synced.embedding_profile, "test needs a real switch"
    # Point the index at the new spec and make config agree.
    config.save_config({"embedding_model": "static"})
    _set_stored_embedder(synced, new_spec)

    synced._sync_spec()
    assert synced.embedding_profile == new_spec
    assert synced._embedder is None, "the old model must be dropped on adoption"


# --- failure recovery ---------------------------------------------------------

def test_transient_write_error_retries_instead_of_wiping_the_index(synced, monkeypatch):
    """One flaky write must not drop the whole collection.

    The previous logic rebuilt on the *first* exception, so a locking conflict,
    a full disk or an interrupted write destroyed the index rather than being
    retried.
    """
    before = synced.stats()["chunks"]
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")

    assert synced._write(flaky, "flaky") is True
    assert calls["n"] == 2, "should have retried the operation itself"
    assert synced.stats()["chunks"] == before, "index must survive a transient error"
    assert synced.needs_full_sync is False


def test_persistent_write_error_rebuilds_as_a_last_resort(synced):
    attempts = {"n": 0}

    def always_failing():
        attempts["n"] += 1
        raise RuntimeError("corrupt segment")

    assert synced._write(always_failing, "broken") is False
    assert attempts["n"] >= 2, "must try twice before resorting to a rebuild"
    assert synced.needs_full_sync is True, "a rebuild must demand a full resync"


def test_rebuild_flags_that_a_full_resync_is_required(synced):
    synced.rebuild()
    assert synced.needs_full_sync is True
    synced.sync(DOCS)
    assert synced.needs_full_sync is False


def test_vault_resyncs_everything_after_a_mid_write_rebuild(tmp_path):
    """A rebuild during a single-note write must not shrink the searchable set."""
    from kyp_mem.vault import Vault
    from tests.conftest import write as write_note

    root = tmp_path / "vault"
    write_note(root, "P/A.md", "# A\n\n## Topic\n\nDistinctive content about hydroponic lettuce.\n")
    write_note(root, "P/B.md", "# B\n\n## Topic\n\nSeparate content about submarine sonar ranges.\n")
    v = Vault(str(root))
    v.ensure_vector_synced()
    assert v.search("hydroponic lettuce", limit=3)

    # Simulate the store having rebuilt itself mid-write: empty, flag raised.
    v.vector.rebuild()
    assert v.vector.stats()["chunks"] == 0

    # The stale "ready" flag must not suppress the resync.
    assert v.search("hydroponic lettuce", limit=3), "vault must resync after a rebuild"
    assert v.search("submarine sonar ranges", limit=3), "the other note must return too"
