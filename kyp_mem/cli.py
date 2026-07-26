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

    un = subparsers.add_parser("uninstall", help="Remove KYP-MEM from Claude Code (hooks + MCP server)")
    un.add_argument("--purge", action="store_true", help="Also delete vault data and config at ~/.kyp-mem")
    doc_parser = subparsers.add_parser("doctor", help="Check installation, config, and index health")
    doc_parser.add_argument("--deep", action="store_true",
                            help="Also exercise the semantic index read/write path")

    comp_parser = subparsers.add_parser(
        "compact", help="Reclaim disk used by the semantic index (orphan sweep + rebuild + vacuum)")
    comp_parser.add_argument("--dry-run", action="store_true", help="Report what would be freed, change nothing")
    comp_parser.add_argument("--no-rebuild", action="store_true",
                             help="Skip the index rebuild (rebuilding is what reclaims deleted slots)")
    comp_parser.add_argument("--purge-legacy", action="store_true",
                             help="Also delete the pre-1.0 shared index directory, if present")

    subparsers.add_parser("reindex", help="Rebuild the semantic index from the markdown vault")

    cfg_parser = subparsers.add_parser("config", help="Get or set configuration values")
    cfg_parser.add_argument("key", nargs="?", help="Config key (e.g. session_model)")
    cfg_parser.add_argument("value", nargs="?", help="Value to set")

    obj_parser = subparsers.add_parser("objective", help="Get or set a project's objective (injected at session start)")
    obj_parser.add_argument("project", nargs="?", help="Project name (defaults to current directory name)")
    obj_parser.add_argument("text", nargs="*", help="Objective text to set (omit to read the current objective)")

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
    elif args.command == "config":
        _run_config(args.key, args.value)
    elif args.command == "objective":
        _run_objective(args.project, " ".join(args.text).strip())
    elif args.command == "uninstall":
        _run_uninstall(purge=args.purge)
    elif args.command == "doctor":
        _run_doctor(deep=args.deep)
    elif args.command == "compact":
        _run_compact(dry_run=args.dry_run, rebuild=not args.no_rebuild, purge_legacy=args.purge_legacy)
    elif args.command == "reindex":
        _run_reindex()
    elif args.command == "hook":
        from .hooks import handle_session_start, handle_post_tool_use, handle_user_prompt, handle_stop
        if args.hook_command == "session-start":
            handle_session_start()
        elif args.hook_command == "post-tool-use":
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

    print("  Where should your vault live?")
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

    claude_settings = Path.home() / ".claude" / "settings.json"
    already_setup = False
    if claude_settings.exists():
        try:
            cs = json.loads(claude_settings.read_text())
            has_mcp = "kyp-mem" in cs.get("mcpServers", {})
            has_hooks = any("kyp-mem" in str(h) for h in cs.get("hooks", {}).get("Stop", []))
            already_setup = has_mcp and has_hooks
        except (json.JSONDecodeError, KeyError):
            pass

    if already_setup:
        print(f"  {G}✓{R} Claude Code already configured (MCP server + hooks)")
        print(f"  {D}  Restart Claude Code if you changed the vault path.{R}")
    else:
        print(f"  {Y}Next step:{R} Connect to Claude Code:")
        print(f"    {Y}kyp-mem setup-claude --global && kyp-mem install-hooks --global{R}")
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
    print("  Claude gets these tools: kyp_search, kyp_session_search, kyp_project_context,")
    print("  kyp_read, kyp_write, kyp_delete, kyp_list, kyp_tags, kyp_related, kyp_recent,")
    print("  kyp_stats, kyp_sessions, kyp_session_create, kyp_objective_get/set")
    print()
    print(f"  {D}To open the web UI anytime:{R} {Y}kyp-mem ui{R}")
    print()


def _is_in_venv(bin_path: Path) -> bool:
    import sys
    if sys.prefix == sys.base_prefix:
        return False
    try:
        bin_path.resolve().relative_to(Path(sys.prefix).resolve())
        return True
    except ValueError:
        return False


def _get_mcp_command() -> tuple[str, list[str]]:
    kyp_mem_bin = shutil.which("kyp-mem")
    npx_bin = shutil.which("npx")

    if kyp_mem_bin and "_npx" not in Path(kyp_mem_bin).parts:
        bin_path = Path(kyp_mem_bin)
        if _is_in_venv(bin_path):
            print(f"  {Y}Warning:{R} kyp-mem found inside a virtual env — this path won't work outside it.")
            if npx_bin:
                print(f"  {D}Using npx for a stable path instead.{R}")
                return npx_bin, ["-y", "kyp-mem", "serve"]
            print(f"  {D}Install globally: npm install -g kyp-mem{R}")
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


def _run_uninstall(purge: bool = False):
    from .config import CONFIG_DIR

    print()
    print(f"  {C}KYP-MEM{R} — Uninstall")
    print()

    # Remove hooks from global settings
    _run_install_hooks(global_config=True, remove=True)

    # Remove MCP server from Claude Code
    claude_bin = shutil.which("claude")
    if claude_bin:
        subprocess.run(
            [claude_bin, "mcp", "remove", "-s", "user", "kyp-mem"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        print(f"  {G}✓{R} MCP server removed from Claude Code")
    else:
        print(f"  {Y}!{R} 'claude' CLI not found — remove kyp-mem MCP server manually")

    if purge:
        import shutil as sh
        if CONFIG_DIR.exists():
            sh.rmtree(CONFIG_DIR)
            print(f"  {G}✓{R} Deleted {CONFIG_DIR} (vault, config, sessions)")
        else:
            print(f"  {D}  {CONFIG_DIR} does not exist{R}")

    print()
    print("  To finish, remove the npm package:")
    print(f"    {Y}npm uninstall -g kyp-mem{R}")
    print()
    if not purge:
        print(f"  {D}Your vault data at {CONFIG_DIR} was kept.{R}")
        print(f"  {D}To delete it too: kyp-mem uninstall --purge{R}")
        print()


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
        for event in ("SessionStart", "PostToolUse", "UserPromptSubmit", "Stop"):
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

    session_start_hooks = hooks.setdefault("SessionStart", [])
    post_tool_hooks = hooks.setdefault("PostToolUse", [])
    prompt_hooks = hooks.setdefault("UserPromptSubmit", [])
    stop_hooks = hooks.setdefault("Stop", [])

    session_start_hooks = [h for h in session_start_hooks if not _has_kyp_hook(h)]
    post_tool_hooks = [h for h in post_tool_hooks if not _has_kyp_hook(h)]
    prompt_hooks = [h for h in prompt_hooks if not _has_kyp_hook(h)]
    stop_hooks = [h for h in stop_hooks if not _has_kyp_hook(h)]

    session_start_hooks.append({
        "hooks": [{"type": "command", "command": f"{mcp_command} hook session-start"}],
    })
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

    hooks["SessionStart"] = session_start_hooks
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
    print("  How it works:")
    print(f"  {D}  • PostToolUse hook captures file edits, writes, and commands{R}")
    print(f"  {D}  • Stop hook compiles the session into a vault note{R}")
    print(f"  {D}  • Notes saved under Sessions/ with timestamps and tags{R}")
    print(f"  {D}  • Sessions with < 3 substantive actions are skipped{R}")
    print()
    print(f"  {C}Done!{R} Restart Claude Code. Sessions will auto-save to your vault.")
    print()


def _run_config(key, value):
    from .config import load_config, save_config

    config = load_config()

    if key is None:
        print(f"\n  {C}KYP-MEM{R} — Configuration\n")
        for k, v in sorted(config.items()):
            print(f"  {k}: {G}{v}{R}")
        print(f"\n  {D}Configurable keys:{R}")
        print(f"  {D}  vault_path      — Path to vault directory{R}")
        print(f"  {D}  session_model   — Claude model for session summarization{R}")
        print(f"  {D}                    (default: claude-sonnet-4-6){R}")
        print(f"  {D}  embedding_model — optional sentence-transformers model for{R}")
        print(f"  {D}                    semantic search (default: built-in MiniLM){R}")
        print()
        return

    if value is None:
        current = config.get(key, f"{Y}(not set){R}")
        print(f"  {key}: {G}{current}{R}")
        return

    config[key] = value
    save_config(config)
    print(f"  {G}✓{R} {key} = {value}")


def _run_objective(project, text):
    from .config import get_vault_path
    from .vault import Vault

    project = project or Path.cwd().name
    vault = Vault(get_vault_path())
    path = f"{project}/Objective.md"

    if not text:
        note = vault.read(path)
        print()
        print(f"  {C}KYP-MEM{R} — Objective for {G}{project}{R}")
        print()
        if not note:
            print(f"  {Y}(not set){R}")
            print(f"  {D}  Set one: kyp-mem objective {project} \"<your goal>\"{R}")
        else:
            content = note.content.strip()
            lines = content.split("\n")
            if lines and lines[0].lstrip().startswith("# "):
                content = "\n".join(lines[1:]).strip()
            print(f"  {content}")
        print()
        return

    content = f"# Objective\n\n{text}\n"
    vault.write_note(path, content, ["objective", project.lower().replace(" ", "-")], {})
    print()
    print(f"  {G}✓{R} Objective saved for {G}{project}{R} ({path})")
    print(f"  {D}  Injected at every session start.{R}")
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


def _run_compact(dry_run: bool = False, rebuild: bool = True, purge_legacy: bool = False):
    from .config import get_vault_path
    from .vault import Vault
    from .maintenance import compact, human_bytes

    print()
    print(f"  {C}KYP-MEM{R} — Compact semantic index")
    print()

    vault = Vault(get_vault_path())
    store = vault.vector
    if store is None:
        print(f"  {Y}✗{R} Semantic index is disabled (KYP_NO_VECTOR is set)")
        print()
        return

    if rebuild and not dry_run:
        print(f"  {D}  Rebuilding from {len(vault.index.notes)} notes...{R}")

    result = compact(vault, rebuild=rebuild, dry_run=dry_run, purge_legacy=purge_legacy)
    if not result.get("ok"):
        print(f"  {Y}✗{R} {result.get('reason', 'failed')}")
        print()
        return

    steps = result["steps"]
    orphans = steps.get("orphans", {}).get("removed", [])
    dropped = steps.get("collections", {}).get("dropped", [])
    vac = steps.get("vacuum", {})

    if dropped:
        print(f"  {G}✓{R} Dropped {len(dropped)} unused collection(s): {', '.join(dropped)}")
    if orphans:
        print(f"  {G}✓{R} Removed {len(orphans)} orphaned segment(s)")
    if steps.get("fulltext", {}).get("optimized"):
        print(f"  {G}✓{R} Merged full-text index segments")
    if vac.get("freed_bytes"):
        print(f"  {G}✓{R} Vacuumed sqlite — {human_bytes(vac['freed_bytes'])} returned")
    if "sync" in steps:
        s = steps["sync"]
        print(f"  {G}✓{R} Re-embedded {s.get('added', 0)} notes")

    legacy = steps.get("legacy", {})
    if steps.get("legacy_removed", {}).get("removed"):
        print(f"  {G}✓{R} Removed legacy shared index "
              f"({human_bytes(steps['legacy_removed']['freed_bytes'])})")
    elif legacy.get("present"):
        print()
        print(f"  {Y}!{R} Legacy shared index found: {human_bytes(legacy['bytes'])}")
        print(f"  {D}    {legacy['path']}{R}")
        print(f"  {D}    Pre-1.0 builds shared one index between every vault in a folder.{R}")
        print(f"  {D}    Delete it with: kyp-mem compact --purge-legacy{R}")

    before, after = result["before_bytes"], result["after_bytes"]
    freed = result["freed_bytes"]
    print()
    if dry_run:
        print(f"  {Y}dry run{R} — nothing was changed")
        print(f"  {D}  Current size: {human_bytes(before)}{R}")
    else:
        pct = (freed / before * 100) if before else 0
        print(f"  {C}{human_bytes(before)} → {human_bytes(after)}{R}  "
              f"({human_bytes(freed)} freed, {pct:.1f}%)")
    print()


def _run_reindex():
    from .config import get_vault_path
    from .vault import Vault

    print()
    print(f"  {C}KYP-MEM{R} — Rebuild semantic index")
    print()

    vault = Vault(get_vault_path())
    store = vault.vector
    if store is None:
        print(f"  {Y}✗{R} Semantic index is disabled (KYP_NO_VECTOR is set)")
        print()
        return

    print(f"  {D}  Embedding {len(vault.index.notes)} notes...{R}")
    store.rebuild()
    summary = vault.sync_vector()
    stats = store.stats()
    if summary.get("ok"):
        print(f"  {G}✓{R} Indexed {stats.get('chunks', 0)} chunks from {stats.get('documents', 0)} notes")
    else:
        print(f"  {Y}✗{R} Reindex failed — see stderr above")
    print()


def _run_doctor(deep: bool = False):
    from .config import CONFIG_FILE, get_vault_path

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

    # Session logs — one file per Claude session, so count them all.
    session_dir = Path.home() / ".kyp-mem" / "sessions"
    if session_dir.exists():
        logs = list(session_dir.glob("current*.jsonl"))
        if logs:
            entries = 0
            for log in logs:
                try:
                    entries += len(log.read_text().strip().split("\n"))
                except OSError:
                    pass
            print(f"  {D}·{R} Active session logs: {len(logs)} file(s), {entries} entries")

    # Semantic index health and footprint
    _doctor_index_section(vault_path, deep=deep)

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


def _doctor_index_section(vault_path: str, deep: bool = False):
    """Report semantic-index size and waste.

    The health check is only run with --deep. It used to run implicitly on
    every process start, which was the main source of both startup latency and
    index bloat.
    """
    try:
        from .vector import VectorStore, vector_enabled
        from .maintenance import inspect, human_bytes
    except ImportError as e:
        print(f"  {Y}✗{R} Semantic index unavailable: {e}")
        return

    if not vector_enabled():
        print(f"  {D}·{R} Semantic index disabled (KYP_NO_VECTOR is set)")
        return

    from .maintenance import legacy_index_report

    store = VectorStore(vault_path)

    legacy = legacy_index_report(store)
    if legacy["present"]:
        print(f"  {Y}!{R} Legacy shared index: {human_bytes(legacy['bytes'])} at {legacy['path']}")
        print(f"  {D}    Pre-1.0 builds gave every vault in a folder the same index, so each")
        print("      sync pruned the others' notes and re-embedded everything. Now unused.")
        print(f"      Delete it with: kyp-mem compact --purge-legacy{R}")

    report = inspect(store.db_path)
    if not report["exists"]:
        print(f"  {D}·{R} Semantic index not built yet — run: kyp-mem reindex")
        return

    print(f"  {G}✓{R} Semantic index: {human_bytes(report['total_bytes'])} on disk "
          f"({report['embeddings']} embeddings)")

    waste = sum(o["bytes"] for o in report["orphans"]) + report["reclaimable_sqlite_bytes"]
    if report["orphans"]:
        print(f"  {Y}!{R} {len(report['orphans'])} orphaned segment(s) — "
              f"{human_bytes(sum(o['bytes'] for o in report['orphans']))}")
    if report["reclaimable_sqlite_bytes"] > 1024 * 1024:
        print(f"  {Y}!{R} {human_bytes(report['reclaimable_sqlite_bytes'])} reclaimable in sqlite freelist")

    stale = [n for _, n in report["collections"] if n != store.collection_name]
    if stale:
        print(f"  {Y}!{R} {len(stale)} unused collection(s): {', '.join(stale)}")

    # An index far larger than its content is the signature of the growth bug.
    if report["embeddings"] and report["total_bytes"] > 50 * 1024 * 1024:
        per_record = report["total_bytes"] / report["embeddings"]
        if per_record > 20_000:
            print(f"  {Y}!{R} {human_bytes(int(per_record))} of index per embedding — "
                  "far above expected; run: kyp-mem compact")

    if waste > 1024 * 1024 or stale:
        print(f"  {D}    Reclaim with: kyp-mem compact{R}")

    if deep:
        ok, detail = store.health_check()
        if ok:
            print(f"  {G}✓{R} Index read/write check passed")
        else:
            print(f"  {Y}✗{R} Index check failed: {detail}")
            print(f"  {D}    Repair with: kyp-mem reindex{R}")
    store.close()


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
