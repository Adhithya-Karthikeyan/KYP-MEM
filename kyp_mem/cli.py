"""KYP-MEM CLI — Know Your Project Memory."""

import os
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="kyp-mem",
        description="KYP-MEM — Know Your Project Memory. Headless Obsidian for AI agents.",
    )
    parser.add_argument("--vault", default=None, help="Override vault path")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Set up vault location (first-time setup)")
    subparsers.add_parser("serve", help="Start MCP server (stdio)")

    ui_parser = subparsers.add_parser("ui", help="Open web UI in browser")
    ui_parser.add_argument("--port", type=int, default=3333, help="Port (default: 3333)")
    ui_parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    subparsers.add_parser("stats", help="Print vault statistics")
    subparsers.add_parser("tree", help="Print vault tree")

    args = parser.parse_args()

    if args.vault:
        os.environ["KYP_VAULT"] = args.vault

    if args.command == "init":
        _run_init()

    elif args.command == "serve":
        from .server import mcp
        mcp.run()

    elif args.command == "ui":
        from .ui import start_ui
        start_ui(port=args.port, open_browser=not args.no_open)

    elif args.command == "stats":
        from .config import get_vault_path
        from .vault import Vault
        vault_path = get_vault_path()
        v = Vault(vault_path)
        s = v.get_stats()
        print(f"\033[36mKYP-MEM\033[0m vault: {vault_path}")
        print(f"  Notes:     \033[32m{s['notes']}\033[0m")
        print(f"  Folders:   \033[32m{s['folders']}\033[0m")
        print(f"  Tags:      \033[32m{s['tags']}\033[0m")
        print(f"  Links:     \033[32m{s['links']}\033[0m")
        print(f"  Backlinks: \033[32m{s['backlinks']}\033[0m")

    elif args.command == "tree":
        from .config import get_vault_path
        from .vault import Vault
        vault_path = get_vault_path()
        v = Vault(vault_path)
        print(f"\033[36mKYP-MEM\033[0m vault: {vault_path}\n")
        _print_tree(v.get_full_tree(), "")

    else:
        _print_banner()
        parser.print_help()


def _run_init():
    from .config import CONFIG_FILE, save_config, load_config, DEFAULT_VAULT

    print()
    print("\033[36m  ██╗  ██╗██╗   ██╗██████╗       ███╗   ███╗███████╗███╗   ███╗\033[0m")
    print("\033[36m  ██║ ██╔╝╚██╗ ██╔╝██╔══██╗      ████╗ ████║██╔════╝████╗ ████║\033[0m")
    print("\033[36m  █████╔╝  ╚████╔╝ ██████╔╝█████╗██╔████╔██║█████╗  ██╔████╔██║\033[0m")
    print("\033[36m  ██╔═██╗   ╚██╔╝  ██╔═══╝ ╚════╝██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║\033[0m")
    print("\033[36m  ██║  ██╗   ██║   ██║           ██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║\033[0m")
    print("\033[36m  ╚═╝  ╚═╝   ╚═╝   ╚═╝           ╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝\033[0m")
    print()
    print("  \033[90mKnow Your Project — Headless Obsidian for AI agents\033[0m")
    print()
    print("  \033[33m>> First-time setup\033[0m")
    print()

    current = load_config()
    current_path = current.get("vault_path", DEFAULT_VAULT)

    print(f"  Where should your vault live?")
    print(f"  \033[90mThis is where all your notes/knowledge will be stored.\033[0m")
    print(f"  \033[90mDefault: {current_path}\033[0m")
    print()

    vault_input = input(f"  Vault path [{current_path}]: ").strip()
    vault_path = vault_input or current_path
    vault_path = str(Path(vault_path).expanduser().resolve())

    Path(vault_path).mkdir(parents=True, exist_ok=True)
    save_config({"vault_path": vault_path})

    print()
    print(f"  \033[32m✓\033[0m Vault:  {vault_path}")
    print(f"  \033[32m✓\033[0m Config: {CONFIG_FILE}")
    print()
    print("  \033[36mYou're ready!\033[0m")
    print()
    print("  \033[33mkyp-mem ui\033[0m       Open web UI")
    print("  \033[33mkyp-mem serve\033[0m    Start MCP server (for Claude)")
    print("  \033[33mkyp-mem stats\033[0m    Show vault statistics")
    print("  \033[33mkyp-mem tree\033[0m     Print vault tree")
    print()


def _print_banner():
    print()
    print("\033[36m  KYP-MEM\033[0m — Know Your Project Memory")
    print("  \033[90mHeadless Obsidian for AI agents\033[0m")
    print()


def _print_tree(node: dict, prefix: str):
    if node["type"] == "folder":
        if node["name"] != "vault":
            print(f"{prefix}\033[36m{node['name']}/\033[0m")
            prefix += "  "
        for child in node.get("children", []):
            _print_tree(child, prefix)
    else:
        name = node["name"].replace(".md", "")
        print(f"{prefix}\033[37m{name}\033[0m")


if __name__ == "__main__":
    main()
