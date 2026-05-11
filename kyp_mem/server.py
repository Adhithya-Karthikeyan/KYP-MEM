"""KYP-MEM MCP server — headless knowledge base for AI agents."""

import os
import json
from mcp.server.fastmcp import FastMCP
from .vault import Vault

vault_path = os.environ.get("KYP_VAULT", os.path.expanduser("~/.kyp-mem/vault"))
vault = Vault(vault_path)

mcp = FastMCP("kyp-mem", description="Know Your Project — headless knowledge base like Obsidian")


@mcp.tool()
def kyp_list(path: str = "") -> str:
    """List notes and folders in the vault. Pass a folder path to list its contents, or empty for root."""
    tree = vault.list_tree(path)
    lines = []
    for f in tree["folders"]:
        lines.append(f"  {f}/")
    for n in tree["notes"]:
        lines.append(f"  {n}")
    if not lines:
        lines.append("(empty vault)")
    header = f"Vault: {path or '/'}"
    return header + "\n" + "\n".join(lines)


@mcp.tool()
def kyp_read(path: str) -> str:
    """Read a note by path (e.g. 'Hedge Engine/Configuration.md'). Returns content + properties + backlinks + related notes."""
    note = vault.read(path)
    if not note:
        return f"Not found: {path}"

    parts = [f"# {note.title}", ""]

    if note.tags or note.properties or note.created:
        parts.append("**Properties:**")
        if note.tags:
            parts.append(f"  tags: {', '.join(note.tags)}")
        if note.created:
            parts.append(f"  created: {note.created}")
        if note.updated:
            parts.append(f"  updated: {note.updated}")
        for k, v in note.properties.items():
            parts.append(f"  {k}: {v}")
        parts.append("")

    parts.append(note.content)

    backlinks = vault.get_backlinks(path)
    if backlinks:
        parts.append("\n---")
        parts.append("**Backlinks:**")
        for bl in backlinks:
            parts.append(f"  <- {bl}")

    related = vault.get_related(path)
    if related:
        parts.append("\n**Related:**")
        for rel_path, score in related[:8]:
            rel_note = vault.index.notes.get(rel_path)
            title = rel_note.title if rel_note else rel_path
            parts.append(f"  {score:.2f} > {title} ({rel_path})")

    return "\n".join(parts)


@mcp.tool()
def kyp_write(path: str, content: str, tags: str = "", properties: str = "") -> str:
    """Create or update a note. Path like 'Project/Note.md'. Tags: comma-separated. Properties: JSON string."""
    if not path.endswith(".md"):
        path += ".md"

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    props = json.loads(properties) if properties else {}

    vault.write_note(path, content, tag_list, props)

    note = vault.index.notes.get(path)
    link_count = len(note.links) if note else 0
    return f"Written: {path} ({len(tag_list)} tags, {link_count} links detected)"


@mcp.tool()
def kyp_delete(path: str) -> str:
    """Delete a note by path."""
    if vault.delete(path):
        return f"Deleted: {path}"
    return f"Not found: {path}"


@mcp.tool()
def kyp_search(query: str, tag: str = "") -> str:
    """Full-text search across all notes. Optionally filter by tag."""
    results = vault.search(query, tag or None)
    if not results:
        return "No results found."

    lines = [f"Search: '{query}'" + (f" [tag: {tag}]" if tag else ""), ""]
    for path, score, snippet in results:
        note = vault.index.notes.get(path)
        title = note.title if note else path
        lines.append(f"  {title} ({path}) — score: {score:.3f}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)


@mcp.tool()
def kyp_tags(tag: str = "") -> str:
    """List all tags with note counts, or get all notes with a specific tag."""
    if tag:
        notes = vault.get_notes_by_tag(tag)
        if not notes:
            return f"No notes tagged '{tag}'"
        return f"Notes tagged '{tag}':\n" + "\n".join(f"  {n}" for n in notes)

    tags = vault.get_tags()
    if not tags:
        return "No tags in vault."
    return "Tags:\n" + "\n".join(f"  {t} ({c})" for t, c in tags.items())


@mcp.tool()
def kyp_related(path: str) -> str:
    """Find notes related to the given note — by backlinks, shared tags, and folder proximity."""
    related = vault.get_related(path)
    if not related:
        return f"No related notes for: {path}"

    lines = [f"Related to {path}:", ""]
    for rel_path, score in related:
        note = vault.index.notes.get(rel_path)
        title = note.title if note else rel_path
        lines.append(f"  {score:.2f} > {title} ({rel_path})")
    return "\n".join(lines)


@mcp.tool()
def kyp_recent(limit: int = 10) -> str:
    """Get recently modified notes."""
    notes = vault.get_recent(limit)
    if not notes:
        return "Vault is empty."

    lines = ["Recent notes:", ""]
    for note in notes:
        date = note.updated or note.created or "?"
        tags = f" [{', '.join(note.tags)}]" if note.tags else ""
        lines.append(f"  {date} — {note.title} ({note.path}){tags}")
    return "\n".join(lines)


@mcp.tool()
def kyp_stats() -> str:
    """Get vault statistics — note count, folders, tags, links."""
    s = vault.get_stats()
    return (
        f"Vault stats:\n"
        f"  Notes: {s['notes']}\n"
        f"  Folders: {s['folders']}\n"
        f"  Tags: {s['tags']}\n"
        f"  Links: {s['links']}\n"
        f"  Backlinks: {s['backlinks']}"
    )
