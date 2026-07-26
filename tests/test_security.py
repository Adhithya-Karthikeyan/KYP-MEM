"""Security regression tests.

Every case here is a confirmed defect found in a pre-1.0 release audit. The
vault's markdown notes and the user's wider filesystem must be unreachable from
untrusted callers — and both the MCP tools and the web API are untrusted: the
agent's path argument is steerable by prompt injection through any note it
reads, and the HTTP API takes paths straight off the wire.
"""


import pytest

from kyp_mem.vault import Vault
from tests.conftest import write

pytestmark = pytest.mark.usefixtures("no_vector")


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    write(root, "P/Knowledge.md", "# Knowledge\n\nbody\n")
    return Vault(str(root), with_vector=False)


@pytest.fixture
def outsider(tmp_path):
    """A file next to the vault that must never be reachable."""
    secret = tmp_path / "outside.txt"
    secret.write_text("do not touch")
    return secret


ESCAPES = [
    "../outside.txt",
    "../../outside.txt",
    "P/../../outside.txt",
    "./../outside.txt",
    "P/../../../../../../etc/passwd",
]


# --- delete -------------------------------------------------------------------

@pytest.mark.parametrize("path", ESCAPES)
def test_delete_cannot_escape_the_vault(vault, outsider, path):
    assert vault.delete(path) is False
    assert outsider.exists(), "delete escaped the vault"


def test_delete_rejects_absolute_paths(vault, outsider):
    assert vault.delete(str(outsider)) is False
    assert outsider.exists()


def test_delete_still_works_for_real_notes(vault, tmp_path):
    assert vault.delete("P/Knowledge.md") is True
    assert not (tmp_path / "vault" / "P" / "Knowledge.md").exists()


def test_delete_refuses_directories(vault, tmp_path):
    assert vault.delete("P") is False
    assert (tmp_path / "vault" / "P").is_dir()


def test_delete_does_not_follow_symlink_out_of_vault(vault, outsider, tmp_path):
    link = tmp_path / "vault" / "P" / "link.md"
    try:
        link.symlink_to(outsider)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert vault.delete("P/link.md") is False
    assert outsider.exists(), "followed a symlink out of the vault"


# --- read ---------------------------------------------------------------------

@pytest.mark.parametrize("path", ESCAPES)
def test_read_cannot_escape_the_vault(vault, outsider, path):
    assert vault.read(path) is None


def test_read_rejects_absolute_paths(vault, outsider):
    assert vault.read(str(outsider)) is None


def test_read_still_works_for_real_notes(vault):
    assert vault.read("P/Knowledge.md") is not None


# --- write --------------------------------------------------------------------

@pytest.mark.parametrize("path", ["../evil.md", "../../evil.md", "P/../../evil.md"])
def test_write_cannot_escape_the_vault(vault, tmp_path, path):
    with pytest.raises(ValueError):
        vault.write_note(path, "payload", [], {})
    assert not (tmp_path / "evil.md").exists()


def test_write_rejects_absolute_paths(vault, tmp_path):
    target = tmp_path / "abs.md"
    with pytest.raises(ValueError):
        vault.write_note(str(target), "payload", [], {})
    assert not target.exists()


def test_write_still_works_for_real_notes(vault, tmp_path):
    vault.write_note("P/New.md", "# New\n\nbody\n", [], {})
    assert (tmp_path / "vault" / "P" / "New.md").exists()


# --- list ---------------------------------------------------------------------

def test_list_tree_cannot_escape_the_vault(vault):
    assert vault.list_tree("../..") == {"folders": [], "notes": []}


# --- MCP surface --------------------------------------------------------------

def test_mcp_write_reports_rejection_instead_of_crashing(vault, tmp_path, monkeypatch):
    from kyp_mem import server

    monkeypatch.setattr(server, "vault", vault)
    out = server.kyp_write("../escaped.md", "payload")
    assert "Rejected" in out
    assert not (tmp_path / "escaped.md").exists()


def test_mcp_delete_reports_not_found_for_escaping_path(vault, outsider, monkeypatch):
    from kyp_mem import server

    monkeypatch.setattr(server, "vault", vault)
    assert "Not found" in server.kyp_delete("../outside.txt")
    assert outsider.exists()


# --- compact --purge-legacy ---------------------------------------------------

def test_purge_legacy_never_targets_a_vault_named_chroma(tmp_path):
    """A vault at <parent>/chroma resolved to the legacy index path.

    `remove_legacy_index` rmtree's whatever that report names, so this would
    have deleted every note in the vault.
    """
    from kyp_mem import maintenance as mt
    from kyp_mem.vector import VectorStore

    vault_dir = tmp_path / "docs" / "chroma"
    vault_dir.mkdir(parents=True)
    (vault_dir / "Knowledge.md").write_text("# Precious\n\nirreplaceable\n")

    store = VectorStore(str(vault_dir))
    assert mt.legacy_index_report(store)["present"] is False
    assert mt.remove_legacy_index(store)["removed"] is False
    assert (vault_dir / "Knowledge.md").exists(), "purge deleted the vault"


def test_purge_legacy_ignores_a_chroma_folder_holding_notes(tmp_path):
    from kyp_mem import maintenance as mt
    from kyp_mem.vector import VectorStore

    vault_dir = tmp_path / "docs" / "memory"
    vault_dir.mkdir(parents=True)
    notes_dir = tmp_path / "docs" / "chroma"
    notes_dir.mkdir()
    (notes_dir / "chroma.sqlite3").write_bytes(b"x" * 100)
    (notes_dir / "Notes.md").write_text("# Mine\n")

    store = VectorStore(str(vault_dir))
    assert mt.legacy_index_report(store)["present"] is False
    assert (notes_dir / "Notes.md").exists()


def test_purge_legacy_ignores_a_directory_that_is_not_a_chroma_store(tmp_path):
    from kyp_mem import maintenance as mt
    from kyp_mem.vector import VectorStore

    vault_dir = tmp_path / "docs" / "memory"
    vault_dir.mkdir(parents=True)
    decoy = tmp_path / "docs" / "chroma"
    decoy.mkdir()
    (decoy / "random.bin").write_bytes(b"not a chroma store")

    store = VectorStore(str(vault_dir))
    assert mt.legacy_index_report(store)["present"] is False
    assert decoy.exists()


def test_purge_legacy_still_removes_a_genuine_legacy_store(tmp_path):
    from kyp_mem import maintenance as mt
    from kyp_mem.vector import VectorStore

    vault_dir = tmp_path / "docs" / "memory"
    vault_dir.mkdir(parents=True)
    legacy = tmp_path / "docs" / "chroma"
    legacy.mkdir()
    (legacy / "chroma.sqlite3").write_bytes(b"x" * 5000)

    store = VectorStore(str(vault_dir))
    assert mt.legacy_index_report(store)["present"] is True
    assert mt.remove_legacy_index(store)["removed"] is True
    assert not legacy.exists()


# --- web UI bind --------------------------------------------------------------

def test_ui_binds_loopback_by_default(monkeypatch):
    import kyp_mem.ui as ui

    captured = {}
    monkeypatch.setattr(ui.uvicorn, "run", lambda app, **kw: captured.update(kw))
    monkeypatch.setattr(ui, "create_app", lambda p: object())
    ui.start_ui(port=1, vault_path="/tmp/x", open_browser=False)
    assert captured["host"] == "127.0.0.1", "unauthenticated vault API must not bind all interfaces"


def test_ui_wide_bind_is_opt_in(monkeypatch):
    import kyp_mem.ui as ui

    captured = {}
    monkeypatch.setattr(ui.uvicorn, "run", lambda app, **kw: captured.update(kw))
    monkeypatch.setattr(ui, "create_app", lambda p: object())
    ui.start_ui(port=1, vault_path="/tmp/x", open_browser=False, host="0.0.0.0")
    assert captured["host"] == "0.0.0.0"
