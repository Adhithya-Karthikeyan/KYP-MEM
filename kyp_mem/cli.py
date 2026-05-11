"""KYP-MEM CLI — Know Your Project Memory."""

import os
import json
import shutil
import argparse
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
    subparsers.add_parser("doctor", help="Check installation and config health")

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
    elif args.command == "doctor":
        _run_doctor()
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
    kyp_mem_bin = shutil.which("kyp-mem")

    if not kyp_mem_bin:
        print(f"  {Y}Warning:{R} 'kyp-mem' not found in PATH.")
        print(f"  {D}Make sure you installed with: pip install kyp-mem{R}")
        print()
        kyp_mem_bin = "kyp-mem"

    if global_config:
        settings_path = Path.home() / ".claude" / "settings.json"
        scope = "global"
    else:
        settings_path = Path.cwd() / ".claude" / "settings.json"
        scope = "project"

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    mcp_servers = settings.setdefault("mcpServers", {})

    mcp_servers["kyp-mem"] = {
        "command": kyp_mem_bin,
        "args": ["serve"],
        "env": {
            "KYP_VAULT": vault_path,
        },
    }

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    print()
    print(f"  {C}KYP-MEM{R} — Claude Code Setup")
    print()
    print(f"  {G}✓{R} MCP server added to {scope} settings")
    print(f"  {D}  File:    {settings_path}{R}")
    print(f"  {D}  Command: {kyp_mem_bin} serve{R}")
    print(f"  {D}  Vault:   {vault_path}{R}")
    print()
    print(f"  {C}Done!{R} Restart Claude Code and kyp-mem will run automatically.")
    print(f"  Claude gets these tools: kyp_list, kyp_read, kyp_write, kyp_delete,")
    print(f"  kyp_search, kyp_tags, kyp_related, kyp_recent, kyp_stats")
    print()
    print(f"  {D}To open the web UI anytime:{R} {Y}kyp-mem ui{R}")
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

    # Claude Code config
    for label, path in [
        ("project", Path.cwd() / ".claude" / "settings.json"),
        ("global", Path.home() / ".claude" / "settings.json"),
    ]:
        if path.exists():
            try:
                s = json.loads(path.read_text())
                if "kyp-mem" in s.get("mcpServers", {}):
                    print(f"  {G}✓{R} Claude Code ({label}): kyp-mem configured")
                else:
                    print(f"  {D}·{R} Claude Code ({label}): exists but kyp-mem not configured")
            except json.JSONDecodeError:
                print(f"  {Y}✗{R} Claude Code ({label}): invalid JSON")
        else:
            print(f"  {D}·{R} Claude Code ({label}): no settings file")

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
