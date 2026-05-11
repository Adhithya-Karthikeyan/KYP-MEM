"""KYP-MEM CLI — Know Your Project Memory."""

import sys
import os
import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="kyp-mem",
        description="KYP-MEM — Know Your Project Memory. Headless Obsidian for AI agents.",
    )
    parser.add_argument("--vault", default=None, help="Vault path (default: ~/.kyp-mem/vault)")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Start MCP server (stdio)")

    ui_parser = subparsers.add_parser("ui", help="Open web UI in browser")
    ui_parser.add_argument("--port", type=int, default=3333, help="Port (default: 3333)")
    ui_parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")

    subparsers.add_parser("stats", help="Print vault statistics")
    subparsers.add_parser("tree", help="Print vault tree")

    args = parser.parse_args()

    if args.vault:
        os.environ["KYP_VAULT"] = args.vault

    if args.command == "serve":
        from .server import mcp
        mcp.run()

    elif args.command == "ui":
        from .ui import start_ui
        start_ui(port=args.port, open_browser=not args.no_open)

    elif args.command == "stats":
        vault_path = os.environ.get("KYP_VAULT", os.path.expanduser("~/.kyp-mem/vault"))
        from .vault import Vault
        v = Vault(vault_path)
        s = v.get_stats()
        print(f"Vault: {vault_path}")
        print(f"  Notes:     {s['notes']}")
        print(f"  Folders:   {s['folders']}")
        print(f"  Tags:      {s['tags']}")
        print(f"  Links:     {s['links']}")
        print(f"  Backlinks: {s['backlinks']}")

    elif args.command == "tree":
        vault_path = os.environ.get("KYP_VAULT", os.path.expanduser("~/.kyp-mem/vault"))
        from .vault import Vault
        v = Vault(vault_path)
        _print_tree(v.get_full_tree(), "")

    else:
        parser.print_help()


def _print_tree(node: dict, prefix: str):
    if node["type"] == "folder":
        if node["name"] != "vault":
            print(f"{prefix}{node['name']}/")
            prefix += "  "
        for child in node.get("children", []):
            _print_tree(child, prefix)
    else:
        print(f"{prefix}{node['name']}")


if __name__ == "__main__":
    main()
