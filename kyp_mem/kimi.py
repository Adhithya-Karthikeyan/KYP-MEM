"""KYP-MEM × Kimi CLI — register the MCP server and session-capture hooks.

Kimi CLI keeps MCP servers in ``$KIMI_CODE_HOME/mcp.json`` (user level) or
``<cwd>/.kimi-code/mcp.json`` (project level), and hooks as ``[[hooks]]`` entries
in ``$KIMI_CODE_HOME/config.toml`` (user level only). Both files are edited
surgically: existing servers, settings, and non-kyp hooks are preserved.

Kimi renders hook stdout as a visible block in the chat UI, so unlike the
Claude Code integration, no context block is printed from any hook — memory
reaches the agent silently through the MCP tools instead
(``kyp_project_context`` at session start, ``kyp_search``/``kyp_session_search``
on demand). The hooks only capture: prompts, tool activity, and session
summaries.
"""

import json
import os
from pathlib import Path

C = "\033[36m"  # cyan
G = "\033[32m"  # green
Y = "\033[33m"  # yellow
D = "\033[90m"  # dim
R = "\033[0m"   # reset

HOOK_MARKER = "# --- kyp-mem session capture (managed by kyp-mem install-kimi-hooks) ---"
HOOK_MATCHER = "Edit|Write|Read|Bash"
STOP_TIMEOUT = 180  # summarization shells out to `claude -p` (up to 120s); default 30s would kill it


def _kimi_home() -> Path:
    """Kimi CLI data dir — KIMI_CODE_HOME first, falling back to ~/.kimi-code."""
    return Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code")


def _toml_str(value: str) -> str:
    """Escape a string for a TOML basic string (Windows paths carry backslashes)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _hook_command_prefix() -> str:
    """Shell prefix that invokes the kyp-mem binary, e.g. ``kyp-mem`` or ``npx -y kyp-mem``."""
    from .cli import _get_mcp_command

    command, args = _get_mcp_command()
    return " ".join([command, *args[:-1]])  # drop the trailing "serve"


def _render_hook_blocks(command_prefix: str) -> str:
    prefix = _toml_str(command_prefix)
    return (
        f"{HOOK_MARKER}\n"
        "[[hooks]]\n"
        'event = "UserPromptSubmit"\n'
        f'command = "{prefix} hook user-prompt"\n'
        "\n"
        "[[hooks]]\n"
        'event = "PostToolUse"\n'
        f'matcher = "{HOOK_MATCHER}"\n'
        f'command = "{prefix} hook post-tool-use"\n'
        "\n"
        "[[hooks]]\n"
        'event = "Stop"\n'
        f'command = "{prefix} hook stop"\n'
        f"timeout = {STOP_TIMEOUT}\n"
    )


def _is_kyp_hook_command(command: str) -> bool:
    """Identify kyp-mem's own hook commands.

    ``kyp-mem hook`` alone is not enough — on Windows the npm shim resolves to
    ``kyp-mem.cmd``, so the token and `` hook `` are matched separately.
    """
    return "kyp-mem" in command and " hook " in command


def _strip_kyp_hook_blocks(text: str) -> str:
    """Remove every ``[[hooks]]`` block mentioning ``kyp-mem hook`` (and our marker
    comment above it), preserving all other content byte-for-byte."""
    lines = text.split("\n")
    out = []
    i = 0
    removed = False
    while i < len(lines):
        if lines[i].strip() == "[[hooks]]":
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("["):
                j += 1
            block = lines[i:j]
            if any(_is_kyp_hook_command(b) for b in block):
                removed = True
                k = len(out) - 1
                while k >= 0 and out[k].strip() == "":
                    k -= 1
                if k >= 0 and "--- kyp-mem" in out[k]:
                    del out[k:]
                i = j
                continue
            out.extend(block)
            i = j
            continue
        out.append(lines[i])
        i += 1
    if not removed:
        return text
    # Tidy up the gaps removal left: collapse blank runs, single trailing newline.
    tidied = []
    for line in out:
        if line.strip() == "" and tidied and tidied[-1].strip() == "":
            continue
        tidied.append(line)
    while tidied and tidied[-1].strip() == "":
        tidied.pop()
    return "\n".join(tidied) + "\n" if tidied else ""


def run_setup_kimi(global_config: bool = False, remove: bool = False):
    """Register (or remove) the kyp-mem MCP server in Kimi CLI's mcp.json."""
    from .config import get_vault_path

    if global_config:
        mcp_path = _kimi_home() / "mcp.json"
        scope_label = "global user"
    else:
        mcp_path = Path.cwd() / ".kimi-code" / "mcp.json"
        scope_label = "local project"

    print()
    print(f"  {C}KYP-MEM{R} — Kimi CLI Setup")
    print()

    if not _kimi_home().exists():
        print(f"  {Y}!{R} Kimi data dir not found: {_kimi_home()}")
        print(f"  {D}  Writing config anyway — Kimi CLI will pick it up once installed.{R}")

    data = {}
    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text())
        except json.JSONDecodeError as e:
            print(f"  {Y}✗{R} {mcp_path} is not valid JSON — leaving it untouched")
            print(f"  {D}  {e}{R}")
            print()
            return
        if not isinstance(data, dict):
            print(f"  {Y}✗{R} {mcp_path} has an unexpected shape — leaving it untouched")
            print()
            return

    servers = data.setdefault("mcpServers", {})

    if remove:
        if "kyp-mem" not in servers:
            print(f"  {D}· kyp-mem is not registered in {mcp_path} — nothing to remove{R}")
            print()
            return
        del servers["kyp-mem"]
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        mcp_path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"  {G}✓{R} MCP server removed from Kimi CLI ({scope_label})")
        print(f"  {D}  File: {mcp_path}{R}")
        print()
        return

    from .cli import _get_mcp_command

    mcp_command, mcp_args = _get_mcp_command()
    vault_path = get_vault_path()

    servers["kyp-mem"] = {
        "command": mcp_command,
        "args": mcp_args,
        "env": {"KYP_VAULT": vault_path},
    }

    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps(data, indent=2) + "\n")

    print(f"  {G}✓{R} MCP server registered with Kimi CLI ({scope_label})")
    print(f"  {D}  File:    {mcp_path}{R}")
    print(f"  {D}  Command: {mcp_command} {' '.join(mcp_args)}{R}")
    print(f"  {D}  Vault:   {vault_path}{R}")
    print()
    print(f"  {C}Done!{R} Start a new Kimi CLI session and kyp-mem will run automatically.")
    print("  Kimi gets these tools: kyp_search, kyp_session_search, kyp_project_context,")
    print("  kyp_read, kyp_write, kyp_delete, kyp_list, kyp_tags, kyp_related, kyp_recent,")
    print("  kyp_stats, kyp_sessions, kyp_session_create, kyp_objective_get/set")
    print()
    print(f"  {D}Next step for session capture:{R} {Y}kyp-mem install-kimi-hooks{R}")
    print()


def run_install_kimi_hooks(remove: bool = False):
    """Install (or remove) kyp-mem's session-capture hooks in Kimi CLI's config.toml."""
    config_path = _kimi_home() / "config.toml"

    print()
    print(f"  {C}KYP-MEM{R} — Kimi CLI Auto-Learning Hooks")
    print()

    original = ""
    if config_path.exists():
        original = config_path.read_text()

    filtered = _strip_kyp_hook_blocks(original)

    if remove:
        if filtered == original:
            print(f"  {D}· No kyp-mem hooks in {config_path} — nothing to remove{R}")
            print()
            return
        config_path.write_text(filtered)
        print(f"  {G}✓{R} KYP-MEM hooks removed from Kimi CLI")
        print(f"  {D}  File: {config_path}{R}")
        print()
        return

    prefix = _hook_command_prefix()
    block = _render_hook_blocks(prefix)

    base = filtered
    if base and not base.endswith("\n"):
        base += "\n"
    if base.strip():
        base += "\n"
    new_text = base + block

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(new_text)

    print(f"  {G}✓{R} Hooks installed in Kimi CLI")
    print(f"  {D}  File: {config_path}{R}")
    print()
    print("  How it works:")
    print(f"  {D}  • UserPromptSubmit captures your prompts{R}")
    print(f"  {D}  • PostToolUse hook captures file edits, writes, reads, and commands{R}")
    print(f"  {D}  • Stop hook compiles the session into a vault note{R}")
    print(f"  {D}  • Notes saved under Sessions/ with timestamps and tags{R}")
    print(f"  {D}  • All hooks are silent — memory reaches the agent via the MCP tools{R}")
    print(f"  {D}    (kyp_project_context at session start, kyp_search on demand){R}")
    print()
    print(f"  {C}Done!{R} Start a new Kimi CLI session. Sessions will auto-save to your vault.")
    print()
