"""End-to-end retrieval quality — the regression net for the tuned thresholds.

The similarity floors (per-embedder, in embedder.py) and the IDF-coverage
gate in vault.py were calibrated against measured score distributions, not
chosen by feel. These tests pin that calibration: relevant queries must find
the right note, and off-topic queries must return *nothing* rather than a
confident wrong answer.

If one of these fails after a threshold change, re-measure the distributions
before adjusting the test — the measurement comments on the floor values in
embedder.py are the justification for the current numbers.
"""

import pytest

from kyp_mem.vault import Vault
from tests.conftest import write

pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

pytestmark = pytest.mark.slow


NOTES = {
    "KYP-MEM/Knowledge.md": """# Knowledge

## Architecture

The MCP server exposes vault tools over stdio and is launched by Claude Code.
See [[Objective]] for more.

## Bugs

### Fixed

The stop hook raced on a shared activity log file, so two concurrent sessions
cross-contaminated each other's summaries into the wrong project.
""",
    "KYP-MEM/Objective.md": """# Objective

Ship kyp-mem as a public product: fast, accurate, persistent memory for agents.
""",
    "KYP-MEM/Sessions/2026-07-21_170312.md": """# Session 2026-07-21_170312

## Summary

Investigated runaway disk consumption in the persistence layer.

## LEARNED

Chroma tombstones deleted vectors and never returns the space to the filesystem,
so the index kept growing to hundreds of megabytes even though only a handful of
notes actually existed.

## COMPLETED

Added `_prune_stale_logs` plus an orphan segment sweep and a vacuum step.
""",
    "GreenLeaf/Knowledge.md": """# GreenLeaf

## Overview

A gardening companion app that tracks watering schedules for houseplants.

## Features

Species lookup, soil moisture reminders, and a photo journal of plant growth.
""",
}


@pytest.fixture(scope="module")
def vault(tmp_path_factory):
    root = tmp_path_factory.mktemp("quality") / "vault"
    for rel, body in NOTES.items():
        write(root, rel, body)
    v = Vault(str(root))
    v.ensure_vector_synced()
    return v


@pytest.mark.parametrize(
    "query,expected",
    [
        # Pure paraphrase — shares almost no vocabulary with the note.
        ("why does disk usage keep growing after deleting things",
         "KYP-MEM/Sessions/2026-07-21_170312.md"),
        # Exact identifier — the case semantic-only retrieval is worst at.
        ("_prune_stale_logs", "KYP-MEM/Sessions/2026-07-21_170312.md"),
        # A Knowledge.md note. The previous index embedded only Sessions/, so
        # this was unreachable by semantic search entirely.
        ("two sessions overwriting each other", "KYP-MEM/Knowledge.md"),
        # Intent match across projects.
        ("how do I remember to water my plants", "GreenLeaf/Knowledge.md"),
        # Short note whose similarity (0.19) sits below the strict floor and is
        # only recovered because the keyword index corroborates it.
        ("what is the product goal", "KYP-MEM/Objective.md"),
    ],
)
def test_relevant_query_finds_the_right_note(vault, query, expected):
    hits = vault.search(query, limit=3)
    assert hits, f"{query!r} returned nothing"
    assert hits[0].path == expected, f"{query!r} -> {[h.path for h in hits]}"


@pytest.mark.parametrize(
    "query",
    [
        "thai green curry recipe",          # clips "green" from the GreenLeaf note
        "kubernetes ingress tls termination",
        "premier league fixtures",
        "how to bake sourdough bread",
        "tax return deadline 2026",         # clips "2026" from a session filename
        "best running shoes for marathons",
    ],
)
def test_offtopic_query_returns_nothing(vault, query):
    # A knowledge tool that confidently returns a wrong note is worse than one
    # that admits it found nothing — the agent is told to trust these results.
    assert vault.search(query, limit=3) == []


def test_hits_report_which_ranker_found_them(vault):
    hits = vault.search("_prune_stale_logs", limit=3)
    assert set(hits[0].sources) == {"keyword", "semantic"}


def test_hits_name_the_section_they_came_from(vault):
    hits = vault.search("why does disk usage keep growing after deleting things", limit=1)
    assert hits[0].heading.endswith("Summary")


def test_keyword_only_mode_still_finds_exact_terms(vault):
    hits = vault.search("_prune_stale_logs", limit=3, semantic=False)
    assert hits[0].path == "KYP-MEM/Sessions/2026-07-21_170312.md"


def test_project_filter_scopes_results(vault):
    hits = vault.search("watering schedules", limit=5, project="GreenLeaf")
    assert hits
    assert all(h.path.startswith("GreenLeaf/") for h in hits)


def test_session_search_excludes_knowledge_notes(vault):
    hits = vault.search_sessions("disk usage growing", limit=5)
    assert hits
    assert all("/Sessions/" in h.doc_path for h in hits)


def test_externally_written_note_becomes_semantically_searchable(tmp_path):
    """The cross-process path: hooks write notes, the MCP server searches them.

    The stop hook writes session notes from a short-lived process that never
    loads the vector index. A long-lived MCP server must still pick them up, so
    a refresh has to invalidate the semantic index and the next search re-sync.
    """
    root = tmp_path / "vault"
    write(root, "P/Knowledge.md", "# Knowledge\n\n## Notes\n\nBaseline content about the build system.\n")
    server_view = Vault(str(root))
    server_view.ensure_vector_synced()
    assert not server_view.search("submarine sonar detection ranges", limit=3)

    # A different process (the stop hook) appends a note, vector index untouched.
    hook_view = Vault(str(root), with_vector=False)
    hook_view.write_note(
        "P/Sessions/s9.md",
        "# Session s9\n\n## LEARNED\n\nCalibrated the submarine sonar detection ranges today.\n",
        ["session"],
        {},
    )

    assert server_view.refresh_if_stale() is True
    hits = server_view.search("submarine sonar detection ranges", limit=3)
    assert hits, "server must re-sync and find the externally written note"
    assert hits[0].path == "P/Sessions/s9.md"


def test_deleted_note_stops_being_returned(tmp_path):
    root = tmp_path / "vault"
    write(root, "P/A.md", "# A\n\n## Topic\n\nDistinctive content about hydroponic lettuce yields.\n")
    v = Vault(str(root))
    v.ensure_vector_synced()
    assert v.search("hydroponic lettuce yields", limit=3)

    v.delete("P/A.md")
    assert not v.search("hydroponic lettuce yields", limit=3)
