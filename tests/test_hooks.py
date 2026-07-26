"""Session-start hook tests.

The hook runs at every session start while the user waits, so it reads the few
files it needs directly instead of building a Vault. These tests pin both the
output and that cheapness.
"""

import io
import sys

import pytest

from kyp_mem import hooks
from tests.conftest import write

pytestmark = pytest.mark.usefixtures("no_vector")


OBJECTIVE = "# Objective\n\nShip kyp-mem as a public product.\n"
SESSION_A = """# Session 2026-07-21_170312

## Summary
Fixed the stop hook race on the shared activity log.

## LEARNED
Concurrent sessions shared one log file.

## COMPLETED
Added per-session log files.
"""
SESSION_B = """# Session 2026-07-20_101500

## Summary
Earlier work on the vector store.
"""


@pytest.fixture
def vault_root(tmp_path):
    root = tmp_path / "vault"
    write(root, "kyp-mem/Objective.md", OBJECTIVE)
    write(root, "kyp-mem/Knowledge.md", "# Knowledge\n\nbody\n")
    write(root, "kyp-mem/Sessions/2026-07-21_170312.md", SESSION_A)
    write(root, "kyp-mem/Sessions/2026-07-20_101500.md", SESSION_B)
    write(root, "OtherProject/Sessions/2026-01-01_000000.md", "# Other\n\n## Summary\nnope\n")
    return root


def run_hook(monkeypatch, vault_root, project_name, stdin="{}"):
    monkeypatch.setenv("KYP_VAULT", str(vault_root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", f"/somewhere/{project_name}")
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    hooks.handle_session_start()
    return buf.getvalue()


# --- file discovery -----------------------------------------------------------

def test_finds_objective_and_sessions(vault_root):
    obj, sessions, exists = hooks._session_context_files(vault_root, "kyp-mem")
    assert exists is True
    assert obj is not None and obj.name == "Objective.md"
    assert [s.name for s in sessions] == [
        "2026-07-21_170312.md",
        "2026-07-20_101500.md",
    ], "newest session first"


def test_project_folder_matched_case_insensitively(vault_root):
    # The working directory is "KYP-MEM"; the vault folder is "kyp-mem".
    obj, sessions, exists = hooks._session_context_files(vault_root, "KYP-MEM")
    assert exists is True
    assert obj is not None
    assert len(sessions) == 2


def test_unknown_project_reports_cleanly(vault_root):
    obj, sessions, exists = hooks._session_context_files(vault_root, "NotAProject")
    assert (obj, sessions, exists) == (None, [], False)


def test_session_limit_is_respected(tmp_path):
    root = tmp_path / "v"
    for i in range(25):
        write(root, f"P/Sessions/2026-01-{i:02d}_000000.md", f"# S{i}\n\n## Summary\nbody {i}\n")
    _, sessions, _ = hooks._session_context_files(root, "P", limit=10)
    assert len(sessions) == 10
    assert sessions[0].name.startswith("2026-01-24"), "newest kept"


def test_project_without_sessions_still_finds_objective(tmp_path):
    root = tmp_path / "v"
    write(root, "P/Objective.md", OBJECTIVE)
    obj, sessions, exists = hooks._session_context_files(root, "P")
    assert exists is True and obj is not None and sessions == []


def test_non_markdown_files_are_ignored(tmp_path):
    root = tmp_path / "v"
    write(root, "P/Sessions/real.md", "# R\n\n## Summary\nx\n")
    (root / "P" / "Sessions" / "notes.txt").write_text("ignore me")
    _, sessions, _ = hooks._session_context_files(root, "P")
    assert [s.name for s in sessions] == ["real.md"]


# --- objective parsing --------------------------------------------------------

def test_objective_text_strips_leading_heading(vault_root):
    obj, _, _ = hooks._session_context_files(vault_root, "kyp-mem")
    assert hooks._objective_text_from(obj) == "Ship kyp-mem as a public product."


def test_objective_text_handles_frontmatter(tmp_path):
    root = tmp_path / "v"
    write(root, "P/Objective.md", "---\ntags: [objective]\n---\n\n# Objective\n\nBe fast.\n")
    obj, _, _ = hooks._session_context_files(root, "P")
    assert hooks._objective_text_from(obj) == "Be fast."


def test_missing_objective_returns_none():
    assert hooks._objective_text_from(None) is None


# --- rendered output ----------------------------------------------------------

def test_output_contains_objective_and_sessions(monkeypatch, vault_root):
    out = run_hook(monkeypatch, vault_root, "KYP-MEM")
    assert "# [kyp-mem] KYP-MEM — Session Context" in out
    assert "## 🎯 Objective" in out
    assert "Ship kyp-mem as a public product." in out
    assert "## Last 2 Sessions" in out
    assert "Session 2026-07-21_170312" in out


def test_output_includes_session_summary_sections(monkeypatch, vault_root):
    out = run_hook(monkeypatch, vault_root, "KYP-MEM")
    assert "Fixed the stop hook race" in out
    assert "**Learned:**" in out


def test_other_projects_are_not_leaked(monkeypatch, vault_root):
    out = run_hook(monkeypatch, vault_root, "KYP-MEM")
    assert "OtherProject" not in out
    assert "nope" not in out


def test_missing_objective_asks_the_user(monkeypatch, tmp_path):
    root = tmp_path / "v"
    write(root, "P/Sessions/s.md", "# S\n\n## Summary\nbody\n")
    out = run_hook(monkeypatch, root, "P")
    assert "Objective — NOT SET" in out
    assert 'kyp_objective_set(project="P"' in out


def test_unknown_project_still_asks_for_an_objective(monkeypatch, vault_root):
    out = run_hook(monkeypatch, vault_root, "BrandNewThing")
    assert "Objective — NOT SET" in out
    assert "Last" not in out.split("Objective — NOT SET")[1].split("CRITICAL")[0]


def test_hook_never_raises_on_a_missing_vault(monkeypatch, tmp_path):
    out = run_hook(monkeypatch, tmp_path / "does-not-exist", "P")
    assert "Objective — NOT SET" in out


def test_hook_is_silent_for_summarizer_subprocess(monkeypatch, vault_root):
    monkeypatch.setenv("KYP_MEM_SUMMARIZING", "1")
    assert run_hook(monkeypatch, vault_root, "KYP-MEM") == ""


# --- the point of the rewrite -------------------------------------------------

def test_hook_cost_does_not_scale_with_vault_size(monkeypatch, tmp_path):
    """Reading 11 files must not depend on the other 1,200 notes.

    Building a Vault took 906 ms on the real 1,281-note vault because it parsed
    every note and built the keyword index. The hook now touches only the files
    it renders, so an unrelated project growing must not slow session start.
    """
    root = tmp_path / "v"
    write(root, "P/Objective.md", OBJECTIVE)
    write(root, "P/Sessions/2026-01-01_000000.md", SESSION_A)
    # A large unrelated project in the same vault.
    for i in range(400):
        write(root, f"Noise/Sessions/2026-02-{i:04d}.md", "# N\n\n## Summary\n" + "x " * 200)

    read_files = []
    real_read = hooks.Path.read_text

    def counting_read(self, *a, **kw):
        read_files.append(str(self))
        return real_read(self, *a, **kw)

    monkeypatch.setattr(hooks.Path, "read_text", counting_read)
    out = run_hook(monkeypatch, root, "P")

    assert "Ship kyp-mem as a public product." in out
    assert len(read_files) <= 12, f"read {len(read_files)} files, expected ~2"
    assert not any("Noise" in f for f in read_files), "must not touch other projects"
