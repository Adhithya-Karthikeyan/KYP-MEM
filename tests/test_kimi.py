"""Tests for the Kimi CLI integration (kyp_mem/kimi.py)."""

import json
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

from kyp_mem import cli as cli_mod
from kyp_mem import kimi as kimi_mod

MCP_CMD = ("/usr/local/bin/kyp-mem", ["serve"])

USER_CONFIG = 'default_model = "kimi-code/k3"\ntelemetry = false\n'
USER_HOOK = (
    "[[hooks]]\n"
    'event = "PreToolUse"\n'
    'matcher = "Bash"\n'
    'command = "node ~/.kimi-code/hooks/check-bash.mjs"\n'
    "timeout = 5\n"
)


@pytest.fixture
def kimi_home(tmp_path, monkeypatch):
    home = tmp_path / "kimi-home"
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    return home


@pytest.fixture
def mcp_cmd(monkeypatch):
    monkeypatch.setattr(cli_mod, "_get_mcp_command", lambda: MCP_CMD)


def _read_mcp(path: Path):
    return json.loads(path.read_text())


def _parse_toml(path: Path):
    if tomllib is None:
        pytest.skip("tomllib requires Python 3.11+")
    return tomllib.loads(path.read_text())


# --- setup-kimi ---------------------------------------------------------------


def test_setup_kimi_global_creates_mcp_json(kimi_home, mcp_cmd):
    kimi_mod.run_setup_kimi(global_config=True)

    mcp_file = kimi_home / "mcp.json"
    assert mcp_file.exists()
    entry = _read_mcp(mcp_file)["mcpServers"]["kyp-mem"]
    assert entry["command"] == MCP_CMD[0]
    assert entry["args"] == MCP_CMD[1]
    assert entry["env"]["KYP_VAULT"]  # conftest points KYP_VAULT at a tmp dir


def test_setup_kimi_project_scope(tmp_path, monkeypatch, kimi_home, mcp_cmd):
    monkeypatch.chdir(tmp_path)
    kimi_mod.run_setup_kimi()

    mcp_file = tmp_path / ".kimi-code" / "mcp.json"
    assert mcp_file.exists()
    assert "kyp-mem" in _read_mcp(mcp_file)["mcpServers"]
    assert not (kimi_home / "mcp.json").exists()


def test_setup_kimi_preserves_existing_servers(kimi_home, mcp_cmd):
    kimi_home.mkdir(parents=True)
    existing = {
        "mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "server-fs"]}},
        "otherTopLevel": True,
    }
    (kimi_home / "mcp.json").write_text(json.dumps(existing))

    kimi_mod.run_setup_kimi(global_config=True)

    data = _read_mcp(kimi_home / "mcp.json")
    assert data["mcpServers"]["filesystem"] == existing["mcpServers"]["filesystem"]
    assert data["mcpServers"]["kyp-mem"]["command"] == MCP_CMD[0]
    assert data["otherTopLevel"] is True


def test_setup_kimi_idempotent(kimi_home, mcp_cmd):
    kimi_mod.run_setup_kimi(global_config=True)
    first = (kimi_home / "mcp.json").read_text()
    kimi_mod.run_setup_kimi(global_config=True)
    assert (kimi_home / "mcp.json").read_text() == first


def test_setup_kimi_remove(kimi_home, mcp_cmd, capsys):
    kimi_mod.run_setup_kimi(global_config=True)
    data = _read_mcp(kimi_home / "mcp.json")
    data["mcpServers"]["other"] = {"command": "x"}
    (kimi_home / "mcp.json").write_text(json.dumps(data))

    kimi_mod.run_setup_kimi(global_config=True, remove=True)
    servers = _read_mcp(kimi_home / "mcp.json")["mcpServers"]
    assert "kyp-mem" not in servers
    assert servers["other"] == {"command": "x"}

    before = (kimi_home / "mcp.json").read_text()
    kimi_mod.run_setup_kimi(global_config=True, remove=True)
    assert "nothing to remove" in capsys.readouterr().out
    assert (kimi_home / "mcp.json").read_text() == before


def test_setup_kimi_invalid_json_is_untouched(kimi_home, mcp_cmd, capsys):
    kimi_home.mkdir(parents=True)
    broken = "{ not valid json"
    (kimi_home / "mcp.json").write_text(broken)

    kimi_mod.run_setup_kimi(global_config=True)

    assert (kimi_home / "mcp.json").read_text() == broken
    assert "not valid JSON" in capsys.readouterr().out


# --- install-kimi-hooks -------------------------------------------------------


def _kyp_hook_entries(config_path: Path):
    return [h for h in _parse_toml(config_path)["hooks"]
            if kimi_mod._is_kyp_hook_command(h["command"])]


def test_install_hooks_creates_config(kimi_home, mcp_cmd):
    kimi_mod.run_install_kimi_hooks()

    config = kimi_home / "config.toml"
    assert config.exists()
    hooks = _kyp_hook_entries(config)
    assert [h["event"] for h in hooks] == ["UserPromptSubmit", "PostToolUse", "Stop"]

    by_event = {h["event"]: h for h in hooks}
    assert by_event["UserPromptSubmit"]["command"].endswith("hook user-prompt")
    assert "matcher" not in by_event["UserPromptSubmit"]
    assert by_event["PostToolUse"]["matcher"] == "Edit|Write|Read|Bash"
    assert by_event["PostToolUse"]["command"].endswith("hook post-tool-use")
    assert by_event["Stop"]["command"].endswith("hook stop")
    assert by_event["Stop"]["timeout"] == 180  # summarization outlives the 30s default
    # Only the four documented fields may appear — extras break Kimi's config load.
    for h in hooks:
        assert set(h) <= {"event", "matcher", "command", "timeout"}


def test_install_hooks_preserves_existing_config(kimi_home, mcp_cmd):
    kimi_home.mkdir(parents=True)
    (kimi_home / "config.toml").write_text(USER_CONFIG + "\n" + USER_HOOK)

    kimi_mod.run_install_kimi_hooks()

    text = (kimi_home / "config.toml").read_text()
    assert USER_CONFIG in text
    assert "check-bash.mjs" in text
    hooks = _parse_toml(kimi_home / "config.toml")["hooks"]
    assert len(hooks) == 4  # user's PreToolUse + our three
    assert hooks[0]["event"] == "PreToolUse"  # user content stays first, untouched


def test_install_hooks_idempotent(kimi_home, mcp_cmd):
    kimi_mod.run_install_kimi_hooks()
    kimi_mod.run_install_kimi_hooks()

    text = (kimi_home / "config.toml").read_text()
    assert text.count("kyp-mem hook") == 3
    assert len(_kyp_hook_entries(kimi_home / "config.toml")) == 3


def test_remove_hooks(kimi_home, mcp_cmd):
    kimi_home.mkdir(parents=True)
    (kimi_home / "config.toml").write_text(USER_CONFIG + "\n" + USER_HOOK)
    kimi_mod.run_install_kimi_hooks()

    kimi_mod.run_install_kimi_hooks(remove=True)

    text = (kimi_home / "config.toml").read_text()
    assert "kyp-mem" not in text
    assert USER_CONFIG in text
    assert "check-bash.mjs" in text
    hooks = _parse_toml(kimi_home / "config.toml")["hooks"]
    assert len(hooks) == 1 and hooks[0]["event"] == "PreToolUse"


def test_remove_hooks_when_none_installed(kimi_home, mcp_cmd, capsys):
    kimi_mod.run_install_kimi_hooks(remove=True)
    assert "nothing to remove" in capsys.readouterr().out
    assert not (kimi_home / "config.toml").exists()


def test_hook_command_escapes_windows_paths(kimi_home, monkeypatch):
    monkeypatch.setattr(
        cli_mod, "_get_mcp_command",
        lambda: ("C:\\Users\\a b\\kyp-mem.cmd", ["serve"]),
    )
    kimi_mod.run_install_kimi_hooks()

    hooks = _kyp_hook_entries(kimi_home / "config.toml")
    assert hooks[0]["command"] == "C:\\Users\\a b\\kyp-mem.cmd hook user-prompt"
