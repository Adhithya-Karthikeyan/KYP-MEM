"""KYP-MEM CLI — Know Your Project Memory."""

import os
import json
import shutil
import argparse
import subprocess
from pathlib import Path

C = "\033[36m"  # cyan
G = "\033[32m"  # green
Y = "\033[33m"  # yellow
D = "\033[90m"  # dim
R = "\033[0m"   # reset


def main():
    parser = argparse.ArgumentParser(
        prog="kyp-mem",
        description="KYP-MEM — Know Your Project Memory. Headless knowledge base for AI agents.",
    )
    parser.add_argument("--vault", default=None, help="Override vault path")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Set up vault location (first-time setup)")
    subparsers.add_parser("serve", help="Start MCP server (stdio)")

    sc = subparsers.add_parser("setup-claude", help="Auto-configure Claude Code to use KYP-MEM")
    sc.add_argument("--global", dest="global_config", action="store_true",
                    help="Add to global ~/.claude/settings.json (default: project .claude/settings.json)")

    ui_parser = subparsers.add_parser("ui", help="Open web UI in browser")
    ui_parser.add_argument("--port", type=int, default=3333, help="Port (default: 3333)")
    ui_parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    subparsers.add_parser("stats", help="Print vault statistics")
    subparsers.add_parser("tree", help="Print vault tree")
    ih = subparsers.add_parser("install-hooks", help="Set up auto-learning hooks for Claude Code")
    ih.add_argument("--global", dest="global_config", action="store_true",
                    help="Add hooks to global ~/.claude/settings.json (default: project)")
    ih.add_argument("--remove", action="store_true", help="Remove KYP-MEM hooks")

    subparsers.add_parser("doctor", help="Check installation and config health")

    hook_parser = subparsers.add_parser("hook", help="Handle Claude Code hook events (internal)")
    hook_sub = hook_parser.add_subparsers(dest="hook_command")
    hook_sub.add_parser("session-start", help="Inject project context at session start")
    hook_sub.add_parser("post-tool-use", help="Capture tool activity to session log")
    hook_sub.add_parser("user-prompt", help="Capture user prompt to session log")
    hook_sub.add_parser("stop", help="Compile session into vault note")

    args = parser.parse_args()

    if args.vault:
        os.environ["KYP_VAULT"] = args.vault

    if args.command == "init":
        _run_init()
    elif args.command == "serve":
        from .server import mcp
        mcp.run()
    elif args.command == "setup-claude":
        _run_setup_claude(global_config=args.global_config)
    elif args.command == "ui":
        from .ui import start_ui
        start_ui(port=args.port, open_browser=not args.no_open)
    elif args.command == "stats":
        _run_stats()
    elif args.command == "tree":
        _run_tree()
    elif args.command == "install-hooks":
        _run_install_hooks(global_config=args.global_config, remove=args.remove)
    elif args.command == "doctor":
        _run_doctor()
    elif args.command == "hook":
        from .hooks import handle_post_tool_use, handle_user_prompt, handle_stop
        if args.hook_command == "post-tool-use":
            handle_post_tool_use()
        elif args.hook_command == "user-prompt":
            handle_user_prompt()
        elif args.hook_command == "stop":
            handle_stop()
    else:
        _print_banner()
        parser.print_help()


def _run_init():
    from .config import CONFIG_FILE, save_config, load_config, DEFAULT_VAULT

    print()
    print(f"{C}  ██╗  ██╗██╗   ██╗██████╗       ███╗   ███╗███████╗███╗   ███╗{R}")
    print(f"{C}  ██║ ██╔╝╚██╗ ██╔╝██╔══██╗      ████╗ ████║██╔════╝████╗ ████║{R}")
    print(f"{C}  █████╔╝  ╚████╔╝ ██████╔╝█████╗██╔████╔██║█████╗  ██╔████╔██║{R}")
    print(f"{C}  ██╔═██╗   ╚██╔╝  ██╔═══╝ ╚════╝██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║{R}")
    print(f"{C}  ██║  ██╗   ██║   ██║           ██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║{R}")
    print(f"{C}  ╚═╝  ╚═╝   ╚═╝   ╚═╝           ╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝{R}")
    print()
    print(f"  {D}Know Your Project — Headless knowledge base for AI agents{R}")
    print()
    print(f"  {Y}>> First-time setup{R}")
    print()

    current = load_config()
    current_path = current.get("vault_path", DEFAULT_VAULT)

    print(f"  Where should your vault live?")
    print(f"  {D}This is where all your notes/knowledge will be stored.{R}")
    print(f"  {D}Default: {current_path}{R}")
    print()

    vault_input = input(f"  Vault path [{current_path}]: ").strip()
    vault_path = vault_input or current_path
    vault_path = str(Path(vault_path).expanduser().resolve())

    Path(vault_path).mkdir(parents=True, exist_ok=True)
    save_config({"vault_path": vault_path})

    print()
    print(f"  {G}✓{R} Vault:  {vault_path}")
    print(f"  {G}✓{R} Config: {CONFIG_FILE}")
    print()
    print(f"  {Y}Next step:{R} Connect to Claude Code:")
    print(f"    {Y}kyp-mem setup-claude{R}")
    print()


def _run_setup_claude(global_config: bool = False):
    from .config import get_vault_path

    vault_path = get_vault_path()
    mcp_command, mcp_args = _get_mcp_command()
    claude_scope = "user" if global_config else "local"
    scope_label = "global user" if global_config else "local project"

    registered, detail = _register_with_claude_mcp(
        claude_scope,
        mcp_command,
        mcp_args,
        vault_path,
    )

    print()
    print(f"  {C}KYP-MEM{R} — Claude Code Setup")
    print()
    if registered:
        print(f"  {G}✓{R} MCP server registered with Claude Code ({scope_label})")
    else:
        settings_path = _write_legacy_claude_settings(global_config, mcp_command, mcp_args, vault_path)
        print(f"  {Y}✗{R} Could not register with Claude Code's MCP manager")
        print(f"  {D}  Reason:  {detail}{R}")
        print(f"  {Y}!{R} Wrote legacy settings as a fallback")
        print(f"  {D}  File:    {settings_path}{R}")
    print(f"  {D}  Command: {mcp_command} {' '.join(mcp_args)}{R}")
    print(f"  {D}  Vault:   {vault_path}{R}")
    print()
    print(f"  {C}Done!{R} Restart Claude Code and kyp-mem will run automatically.")
    print(f"  Claude gets these tools: kyp_list, kyp_read, kyp_write, kyp_delete,")
    print(f"  kyp_search, kyp_tags, kyp_related, kyp_recent, kyp_stats")
    print()
    print(f"  {D}To open the web UI anytime:{R} {Y}kyp-mem ui{R}")
    print()


def _get_mcp_command() -> tuple[str, list[str]]:
    kyp_mem_bin = shutil.which("kyp-mem")
    npx_bin = shutil.which("npx")

    if kyp_mem_bin and "_npx" not in Path(kyp_mem_bin).parts:
        return kyp_mem_bin, ["serve"]
    if npx_bin:
        return npx_bin, ["-y", "kyp-mem", "serve"]

    print(f"  {Y}Warning:{R} 'kyp-mem' not found in PATH.")
    print(f"  {D}Make sure you installed with: npm install -g kyp-mem{R}")
    print()
    return "kyp-mem", ["serve"]


def _register_with_claude_mcp(
    scope: str,
    mcp_command: str,
    mcp_args: list[str],
    vault_path: str,
) -> tuple[bool, str]:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return False, "'claude' CLI not found in PATH"

    server_config = {
        "type": "stdio",
        "command": mcp_command,
        "args": mcp_args,
        "env": {
            "KYP_VAULT": vault_path,
        },
    }

    # Make setup idempotent when the user reruns it with a new vault or binary.
    subprocess.run(
        [claude_bin, "mcp", "remove", "-s", scope, "kyp-mem"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    result = subprocess.run(
        [
            claude_bin,
            "mcp",
            "add-json",
            "-s",
            scope,
            "kyp-mem",
            json.dumps(server_config),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return True, result.stdout.strip()

    detail = (result.stderr or result.stdout or "unknown error").strip()
    return False, detail


def _write_legacy_claude_settings(
    global_config: bool,
    mcp_command: str,
    mcp_args: list[str],
    vault_path: str,
) -> Path:
    if global_config:
        settings_path = Path.home() / ".claude" / "settings.json"
    else:
        settings_path = Path.cwd() / ".claude" / "settings.json"

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    mcp_servers = settings.setdefault("mcpServers", {})
    mcp_servers["kyp-mem"] = {
        "command": mcp_command,
        "args": mcp_args,
        "env": {
            "KYP_VAULT": vault_path,
        },
    }

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings_path


def _run_install_hooks(global_config: bool = False, remove: bool = False):
    mcp_command, _ = _get_mcp_command()

    if global_config:
        settings_path = Path.home() / ".claude" / "settings.json"
        scope_label = "global"
    else:
        settings_path = Path.cwd() / ".claude" / "settings.json"
        scope_label = "project"

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    hooks = settings.setdefault("hooks", {})

    def _has_kyp_hook(entry):
        for hook in entry.get("hooks", []):
            if "kyp-mem hook" in hook.get("command", ""):
                return True
        return "kyp-mem hook" in entry.get("command", "")

    if remove:
        changed = False
        for event in ("PostToolUse", "UserPromptSubmit", "Stop"):
            if event in hooks:
                hooks[event] = [h for h in hooks[event] if not _has_kyp_hook(h)]
                if not hooks[event]:
                    del hooks[event]
                changed = True
        if not hooks:
            del settings["hooks"]
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        print()
        print(f"  {G}✓{R} KYP-MEM hooks removed from {scope_label} settings")
        print(f"  {D}  File: {settings_path}{R}")
        print()
        return

    post_tool_hooks = hooks.setdefault("PostToolUse", [])
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])
    stop_hooks = hooks.setdefault("Stop", [])

    post_tool_hooks = [h for h in post_tool_hooks if not _has_kyp_hook(h)]
    prompt_hooks = [h for h in prompt_hooks if not _has_kyp_hook(h)]
    stop_hooks = [h for h in stop_hooks if not _has_kyp_hook(h)]

    post_tool_hooks.append({
        "matcher": "Edit|Write|Read|Bash",
        "hooks": [{"type": "command", "command": f"{mcp_command} hook post-tool-use"}],
    })
    prompt_hooks.append({
        "hooks": [{"type": "command", "command": f"{mcp_command} hook user-prompt"}],
    })
    stop_hooks.append({
        "hooks": [{"type": "command", "command": f"{mcp_command} hook stop"}],
    })

    hooks["PostToolUse"] = post_tool_hooks
    hooks["UserPromptSubmit"] = prompt_hooks
    hooks["Stop"] = stop_hooks

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    print()
    print(f"  {C}KYP-MEM{R} — Auto-Learning Hooks")
    print()
    print(f"  {G}✓{R} Hooks installed ({scope_label})")
    print(f"  {D}  File: {settings_path}{R}")
    print()
    print(f"  How it works:")
    print(f"  {D}  • PostToolUse hook captures file edits, writes, and commands{R}")
    print(f"  {D}  • Stop hook compiles the session into a vault note{R}")
    print(f"  {D}  • Notes saved under Sessions/ with timestamps and tags{R}")
    print(f"  {D}  • Sessions with < 3 substantive actions are skipped{R}")
    print()
    print(f"  {C}Done!{R} Restart Claude Code. Sessions will auto-save to your vault.")
    print()


def _run_stats():
    from .config import get_vault_path
    from .vault import Vault
    vault_path = get_vault_path()
    v = Vault(vault_path)
    s = v.get_stats()
    print(f"{C}KYP-MEM{R} vault: {vault_path}")
    print(f"  Notes:     {G}{s['notes']}{R}")
    print(f"  Folders:   {G}{s['folders']}{R}")
    print(f"  Tags:      {G}{s['tags']}{R}")
    print(f"  Links:     {G}{s['links']}{R}")
    print(f"  Backlinks: {G}{s['backlinks']}{R}")


def _run_tree():
    from .config import get_vault_path
    from .vault import Vault
    vault_path = get_vault_path()
    v = Vault(vault_path)
    print(f"{C}KYP-MEM{R} vault: {vault_path}\n")
    _print_tree(v.get_full_tree(), "")


def _run_doctor():
    from .config import CONFIG_FILE, load_config, get_vault_path

    print()
    print(f"  {C}KYP-MEM{R} — Health Check")
    print()

    # Config
    if CONFIG_FILE.exists():
        print(f"  {G}✓{R} Config exists: {CONFIG_FILE}")
    else:
        print(f"  {Y}✗{R} No config file. Run: kyp-mem init")

    # Vault
    vault_path = get_vault_path()
    vault_dir = Path(vault_path)
    if vault_dir.exists():
        md_count = len(list(vault_dir.rglob("*.md")))
        print(f"  {G}✓{R} Vault exists: {vault_path} ({md_count} notes)")
    else:
        print(f"  {Y}✗{R} Vault not found: {vault_path}")

    # Claude Code MCP registration
    claude_bin = shutil.which("claude")
    if claude_bin:
        result = subprocess.run(
            [claude_bin, "mcp", "get", "kyp-mem"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "Status: ✓ Connected" in result.stdout:
            print(f"  {G}✓{R} Claude Code MCP: kyp-mem connected")
        elif result.returncode == 0:
            print(f"  {Y}✗{R} Claude Code MCP: kyp-mem registered but not connected")
        else:
            print(f"  {Y}✗{R} Claude Code MCP: kyp-mem not active")
    else:
        print(f"  {Y}✗{R} Claude Code CLI not found in PATH")

    legacy_paths = [
        ("project", Path.cwd() / ".claude" / "settings.json"),
        ("global", Path.home() / ".claude" / "settings.json"),
    ]
    for label, path in legacy_paths:
        if not path.exists():
            continue
        try:
            s = json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"  {Y}✗{R} Legacy Claude settings ({label}): invalid JSON")
            continue
        if "kyp-mem" in s.get("mcpServers", {}):
            print(f"  {D}·{R} Legacy Claude settings ({label}): kyp-mem entry present")

    # Hooks
    for label, path in legacy_paths:
        if not path.exists():
            continue
        try:
            s = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        hooks = s.get("hooks", {})
        has_post = any("kyp-mem hook" in h.get("command", "") for h in hooks.get("PostToolUse", []))
        has_stop = any("kyp-mem hook" in h.get("command", "") for h in hooks.get("Stop", []))
        if has_post and has_stop:
            print(f"  {G}✓{R} Auto-learning hooks installed ({label})")
        elif has_post or has_stop:
            print(f"  {Y}!{R} Partial hooks installed ({label}) — run: kyp-mem install-hooks")

    # Session log
    session_file = Path.home() / ".kyp-mem" / "sessions" / "current.jsonl"
    if session_file.exists():
        line_count = len(session_file.read_text().strip().split("\n"))
        print(f"  {D}·{R} Active session log: {line_count} entries")

    # Binary
    kyp_bin = shutil.which("kyp-mem")
    if kyp_bin:
        print(f"  {G}✓{R} Binary in PATH: {kyp_bin}")
    else:
        print(f"  {Y}✗{R} 'kyp-mem' not found in PATH")

    # MCP dependency
    try:
        import mcp
        print(f"  {G}✓{R} MCP SDK installed: {mcp.__version__}")
    except ImportError:
        print(f"  {Y}✗{R} MCP SDK not installed")
    except AttributeError:
        print(f"  {G}✓{R} MCP SDK installed")

    print()


def _print_banner():
    print()
    print(f"{C}  KYP-MEM{R} — Know Your Project Memory")
    print(f"  {D}Headless knowledge base for AI agents{R}")
    print()


def _print_tree(node: dict, prefix: str):
    if node["type"] == "folder":
        if node["name"] != "vault":
            print(f"{prefix}{C}{node['name']}/{R}")
            prefix += "  "
        for child in node.get("children", []):
            _print_tree(child, prefix)
    else:
        name = node["name"].replace(".md", "")
        print(f"{prefix}{name}")


if __name__ == "__main__":
    main()
