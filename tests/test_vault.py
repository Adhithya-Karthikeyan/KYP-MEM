import pytest

from kyp_mem.vault import Vault, parse_note, serialize_note, Note, is_session_path
from tests.conftest import write

pytestmark = pytest.mark.usefixtures("no_vector")


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    write(root, "KYP-MEM/Knowledge.md", "# Knowledge\n\n## Bugs\n\nThe stop hook races on the shared log.\n")
    write(root, "KYP-MEM/Sessions/2026-07-21.md", "# Session\n\n## LEARNED\n\nChroma never reclaims deleted slots.\n")
    write(root, "GreenLeaf/Knowledge.md", "# GreenLeaf\n\nA gardening app with a plant database.\n")
    return Vault(str(root), with_vector=False)


# --- parsing ------------------------------------------------------------------

def test_parse_frontmatter_tags_and_dates():
    note = parse_note("a.md", "---\ntags: [x, y]\ncreated: 2026-01-01\n---\n\n# Title\n\nbody\n")
    assert note.tags == ["x", "y"]
    assert note.created == "2026-01-01"
    assert note.title == "Title"


def test_parse_comma_separated_tag_string():
    note = parse_note("a.md", "---\ntags: a, b\n---\n\nbody\n")
    assert note.tags == ["a", "b"]


def test_parse_invalid_yaml_does_not_raise():
    note = parse_note("a.md", "---\n: : bad\n---\n\nbody\n")
    assert note.properties == {}


def test_title_falls_back_to_filename():
    assert parse_note("Some Note.md", "no heading here").title == "Some Note"


def test_wikilinks_are_extracted_and_deduped():
    note = parse_note("a.md", "see [[Other]] and [[Other#section]] and [[Third]]")
    assert sorted(note.links) == ["Other", "Third"]


def test_serialize_roundtrip_preserves_tags_and_content():
    note = Note(path="a.md", title="A", content="# A\n\nbody\n", tags=["t"], created="2026-01-01")
    assert parse_note("a.md", serialize_note(note)).tags == ["t"]


def test_is_session_path():
    assert is_session_path("P/Sessions/x.md")
    assert is_session_path("Sessions/x.md")
    assert not is_session_path("P/Knowledge.md")


# --- indexing -----------------------------------------------------------------

def test_loads_all_notes(vault):
    assert len(vault.index.notes) == 3


def test_search_finds_by_content(vault):
    hits = vault.search("chroma reclaims slots", semantic=False)
    assert hits[0].path == "KYP-MEM/Sessions/2026-07-21.md"


def test_search_returns_snippet(vault):
    hits = vault.search("stop hook races", semantic=False)
    assert "stop hook" in hits[0].snippet.lower()


def test_search_filters_by_tag(tmp_path):
    root = tmp_path / "v"
    write(root, "a.md", "---\ntags: [keep]\n---\n\nalpha content\n")
    write(root, "b.md", "alpha content\n")
    v = Vault(str(root), with_vector=False)
    hits = v.search("alpha", tag="keep", semantic=False)
    assert [h.path for h in hits] == ["a.md"]


def test_search_unknown_tag_returns_nothing(vault):
    assert vault.search("stop hook", tag="nope", semantic=False) == []


def test_project_scoped_terms_are_searchable(vault):
    hits = vault.search("greenleaf", semantic=False)
    assert hits[0].path.startswith("GreenLeaf/")


# --- incremental index --------------------------------------------------------

def test_write_note_updates_index_without_full_reload(tmp_path):
    root = tmp_path / "v"
    v = Vault(str(root), with_vector=False)
    v.write_note("P/New.md", "# New\n\nunique_marker_token here\n", ["t"], {})
    assert "P/New.md" in v.index.notes
    assert v.search("unique_marker_token", semantic=False)[0].path == "P/New.md"


def test_rewriting_a_note_removes_its_old_terms(tmp_path):
    root = tmp_path / "v"
    v = Vault(str(root), with_vector=False)
    v.write_note("P/N.md", "# N\n\noldtoken\n", [], {})
    v.write_note("P/N.md", "# N\n\nnewtoken\n", [], {})
    assert not v.search("oldtoken", semantic=False)
    assert v.search("newtoken", semantic=False)[0].path == "P/N.md"


def test_delete_removes_from_index_and_disk(vault, tmp_path):
    assert vault.delete("GreenLeaf/Knowledge.md")
    assert "GreenLeaf/Knowledge.md" not in vault.index.notes
    assert not vault.search("gardening", semantic=False)
    assert not (tmp_path / "vault" / "GreenLeaf" / "Knowledge.md").exists()


def test_delete_missing_note_returns_false(vault):
    assert vault.delete("nope.md") is False


def test_delete_prunes_empty_parent_dirs(vault, tmp_path):
    vault.delete("GreenLeaf/Knowledge.md")
    assert not (tmp_path / "vault" / "GreenLeaf").exists()


def test_write_preserves_created_date_on_update(tmp_path):
    root = tmp_path / "v"
    v = Vault(str(root), with_vector=False)
    v.write_note("P/N.md", "body one", [], {})
    created = v.read("P/N.md").created
    v.write_note("P/N.md", "body two", [], {})
    assert v.read("P/N.md").created == created


# --- staleness ----------------------------------------------------------------

def test_refresh_picks_up_external_add(vault, tmp_path):
    write(tmp_path / "vault", "P/Ext.md", "# Ext\n\nexternally_added_token\n")
    assert vault.refresh_if_stale() is True
    assert vault.search("externally_added_token", semantic=False)[0].path == "P/Ext.md"


def test_refresh_picks_up_external_delete(vault, tmp_path):
    (tmp_path / "vault" / "GreenLeaf" / "Knowledge.md").unlink()
    vault.refresh_if_stale()
    assert "GreenLeaf/Knowledge.md" not in vault.index.notes


def test_refresh_picks_up_external_edit(vault, tmp_path):
    p = tmp_path / "vault" / "GreenLeaf" / "Knowledge.md"
    p.write_text("# GreenLeaf\n\nrewritten_marker\n")
    vault.refresh_if_stale()
    assert vault.search("rewritten_marker", semantic=False)[0].path == "GreenLeaf/Knowledge.md"


def test_refresh_is_a_noop_when_nothing_changed(vault):
    assert vault.refresh_if_stale() is False


# --- links --------------------------------------------------------------------

def test_backlinks_resolve(tmp_path):
    root = tmp_path / "v"
    write(root, "A.md", "# A\n\npoints to [[B]]\n")
    write(root, "B.md", "# B\n\ntarget\n")
    v = Vault(str(root), with_vector=False)
    assert v.get_backlinks("B.md") == ["A.md"]


def test_backlink_appears_when_target_created_later(tmp_path):
    root = tmp_path / "v"
    write(root, "A.md", "# A\n\npoints to [[Later]]\n")
    v = Vault(str(root), with_vector=False)
    assert v.get_backlinks("Later.md") == []
    v.write_note("Later.md", "# Later\n\nnow exists\n", [], {})
    assert v.get_backlinks("Later.md") == ["A.md"]


def test_backlink_disappears_when_source_deleted(tmp_path):
    root = tmp_path / "v"
    write(root, "A.md", "# A\n\n[[B]]\n")
    write(root, "B.md", "# B\n\nx\n")
    v = Vault(str(root), with_vector=False)
    v.delete("A.md")
    assert v.get_backlinks("B.md") == []


def test_note_does_not_backlink_itself(tmp_path):
    root = tmp_path / "v"
    write(root, "A.md", "# A\n\nself ref [[A]]\n")
    v = Vault(str(root), with_vector=False)
    assert v.get_backlinks("A.md") == []


def test_related_scores_backlinks_and_tags(tmp_path):
    root = tmp_path / "v"
    write(root, "P/A.md", "---\ntags: [shared]\n---\n\n# A\n\n[[B]]\n")
    write(root, "P/B.md", "---\ntags: [shared]\n---\n\n# B\n\nx\n")
    write(root, "Q/C.md", "# C\n\nunrelated\n")
    v = Vault(str(root), with_vector=False)
    related = dict(v.get_related("P/A.md"))
    assert "P/B.md" in related
    assert "Q/C.md" not in related


def test_related_of_unknown_note_is_empty(vault):
    assert vault.get_related("nope.md") == []


# --- derived views ------------------------------------------------------------

def test_tags_view(tmp_path):
    root = tmp_path / "v"
    write(root, "a.md", "---\ntags: [x]\n---\n\nbody\n")
    write(root, "b.md", "---\ntags: [x, y]\n---\n\nbody\n")
    v = Vault(str(root), with_vector=False)
    assert v.get_tags() == {"x": 2, "y": 1}
    assert v.get_notes_by_tag("x") == ["a.md", "b.md"]


def test_stats_counts(vault):
    s = vault.get_stats()
    assert s["notes"] == 3
    assert s["folders"] == 2


def test_full_tree_nests_folders(vault):
    tree = vault.get_full_tree()
    names = {c["name"] for c in tree["children"]}
    assert {"KYP-MEM", "GreenLeaf"} <= names


def test_read_missing_note_returns_none(vault):
    assert vault.read("nope.md") is None


def test_search_with_vector_disabled_never_raises(vault):
    assert isinstance(vault.search("anything at all", semantic=True), list)


def test_search_sessions_is_empty_without_vector(vault):
    assert vault.search_sessions("anything") == []
