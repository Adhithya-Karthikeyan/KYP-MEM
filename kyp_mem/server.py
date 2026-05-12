"""KYP-MEM MCP server — headless knowledge base for AI agents."""

import json
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from .config import get_vault_path
from .vault import Vault

vault = Vault(get_vault_path())

mcp = FastMCP("kyp-mem")


@mcp.tool()
def ____kyp_instructions() -> str:
    """## KYP-MEM — Know Your Project Memory

YOU MUST FOLLOW THESE INSTRUCTIONS when kyp-mem tools are available.

### AT SESSION START (MANDATORY)
1. Identify the project you are working on from the user's working directory or question.
2. Call `kyp_project_context(project)` to load the project's knowledge base, notes, and recent session summaries.
3. If no project exists yet, call `kyp_project_context` anyway — if it returns empty, ask the user if you should create one.
4. Use the returned context to ground yourself: understand architecture, known bugs, past decisions, and what was done in recent sessions. Do NOT ask the user questions that are already answered in the project context.

### DURING WORK — WHEN TO SEARCH SESSIONS
Call `kyp_session_search(query)` when:
- You encounter a bug or error — search for it to see if it was investigated before.
- You are about to make an architectural decision — check if a prior session already made this decision.
- The user asks "did we already...", "what happened with...", "last time we..." — search sessions semantically.
- You are unsure about project-specific behavior — sessions contain investigation logs.

### DURING WORK — WHEN TO UPDATE KNOWLEDGE
Call `kyp_write` to update the project's Knowledge.md when you:
- Fix a bug → add it under ## Bugs > ### Fixed
- Discover a new bug → add it under ## Bugs > ### Known
- Make an architectural decision → add it under ## Key Decisions
- Learn something important about the project → add it under ## Notes
- Complete an improvement → move it from ## Improvements > ### Planned to ### Completed

Also use `kyp_write` to create new project notes for substantial topics (API docs, setup guides, component deep-dives). Use [[wikilinks]] to connect notes.

### DURING WORK — WHEN TO USE OTHER TOOLS
- `kyp_search(query)` — keyword search across ALL notes (not just sessions). Use for finding specific content.
- `kyp_read(path)` — read a specific note. Default is brief mode; use full=True for complete content.
- `kyp_related(path)` — find notes connected by backlinks, tags, or folder proximity.
- `kyp_tags(tag)` — browse by tag or list all tags.

### SESSION CAPTURE
Sessions are captured automatically by hooks — you do not need to create session notes manually. If the user explicitly asks to log a session, use `kyp_session_create`.

### IMPORTANT RULES
- NEVER hallucinate project details. If it's not in the knowledge base or sessions, say you don't know.
- ALWAYS check knowledge base before making assumptions about project architecture.
- Keep Knowledge.md updated as you work — it is the source of truth for future sessions.
- Use [[wikilinks]] in notes to build the knowledge graph.
- Tag notes consistently: use project name, topic tags, and type tags (bug, decision, guide, etc.).

Call this tool to acknowledge these instructions. It returns a confirmation."""
    projects = []
    for path in vault.index.notes:
        parts = path.split("/")
        if len(parts) > 1:
            projects.append(parts[0])
    unique = sorted(set(projects))
    return f"KYP-MEM active. Available projects: {', '.join(unique) if unique else '(none)'}. Call kyp_project_context(project) to load context."


@mcp.tool()
def kyp_list(path: str = "") -> str:
    """List notes and folders in the vault. Shows inline tags for quick navigation. Pass a folder path or empty for root."""
    tree = vault.list_tree(path)
    lines = []
    for f in tree["folders"]:
        lines.append(f"  {f}/")
    for n in tree["notes"]:
        rel = f"{path}/{n}" if path else n
        note = vault.index.notes.get(rel)
        if note and note.tags:
            lines.append(f"  {n}  [{', '.join(note.tags)}]")
        else:
            lines.append(f"  {n}")
    if not lines:
        lines.append("(empty vault)")
    header = f"Vault: {path or '/'}"
    return header + "\n" + "\n".join(lines)


@mcp.tool()
def kyp_read(path: str, full: bool = False) -> str:
    """Read a note. Returns brief summary by default (title, tags, preview, links). Set full=True for complete content."""
    note = vault.read(path)
    if not note:
        return f"Not found: {path}"

    if not full:
        parts = [f"# {note.title}"]
        if note.tags:
            parts.append(f"tags: {', '.join(note.tags)}")
        if note.created:
            parts.append(f"created: {note.created}")

        lines = [l for l in note.content.strip().split("\n") if l.strip() and not l.startswith("# ")]
        preview = "\n".join(lines[:6])
        if len(lines) > 6:
            preview += "\n..."
        parts.append("")
        parts.append(preview)

        backlinks = vault.get_backlinks(path)
        outlinks = note.links
        if outlinks:
            parts.append(f"\nlinks: {', '.join(f'[[{l}]]' for l in outlinks)}")
        if backlinks:
            parts.append(f"backlinks: {', '.join(f'[[{b.replace('.md', '')}]]' for b in backlinks)}")

        return "\n".join(parts)

    parts = [f"# {note.title}", ""]

    if note.tags or note.properties or note.created:
        if note.tags:
            parts.append(f"tags: {', '.join(note.tags)}")
        if note.created:
            parts.append(f"created: {note.created}")
        if note.updated:
            parts.append(f"updated: {note.updated}")
        for k, v in note.properties.items():
            parts.append(f"{k}: {v}")
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
    """Create or update a note. Use this to persist knowledge: bug fixes, architectural decisions, setup guides, API contracts. Path like 'Project/Note.md'. Use [[wikilinks]] in content to connect notes. Tags: comma-separated. Properties: JSON string."""
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


@mcp.tool()
def kyp_session_search(query: str, project: str = None) -> str:
    """Semantic search across past session logs using vector embeddings. Use this to recall: what was investigated, bugs encountered, decisions made, and next steps from prior sessions. Search before re-investigating known issues or making decisions that may have been made before."""
    from .vector import get_session_memory
    results = get_session_memory().search_sessions(query, project=project, n_results=5)
    
    if not results or not results.get("ids") or not results["ids"][0]:
        return "No relevant past sessions found."
    
    lines = ["Semantic Session Search Results:", ""]
    for i, path in enumerate(results["ids"][0]):
        doc = results["documents"][0][i]
        score = results["distances"][0][i]
        lines.append(f"--- Session: {path} (Distance: {score:.2f}) ---")
        lines.append(doc)
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def kyp_session_create(project: str, summary: str = "", investigated: str = "", learned: str = "", completed: str = "", next_steps: str = "") -> str:
    """Create a structured session note. Project is required. Sections accept markdown text."""
    session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    parts = [f"# Session {session_id}", ""]
    parts.append(f"**Project:** {project}")
    parts.append("")
    parts.append("## Summary")
    parts.append(summary or "")
    parts.append("")
    parts.append("## INVESTIGATED")
    parts.append(investigated or "")
    parts.append("")
    parts.append("## LEARNED")
    parts.append(learned or "")
    parts.append("")
    parts.append("## COMPLETED")
    parts.append(completed or "")
    parts.append("")
    parts.append("## NEXT STEPS")
    parts.append(next_steps or "")

    content = "\n".join(parts)
    tags = ["session", "manual", project.lower().replace(" ", "-")]
    path = f"{project}/Sessions/{session_id}.md"
    vault.write_note(path, content, tags, {})
    return f"Created session: {path}"


@mcp.tool()
def kyp_sessions(project: str = "", limit: int = 10) -> str:
    """List sessions, optionally filtered by project. Shows most recent first."""
    sessions = []
    for path, note in vault.index.notes.items():
        if "/Sessions/" not in path and not path.startswith("Sessions/"):
            continue
        if project and not path.lower().startswith(project.lower() + "/"):
            continue
        sessions.append((path, note))
    sessions.sort(key=lambda s: s[0], reverse=True)
    sessions = sessions[:limit]
    if not sessions:
        return "No sessions found." + (f" (project filter: {project})" if project else "")
    lines = ["Sessions:", ""]
    for path, note in sessions:
        tags = f" [{', '.join(note.tags)}]" if note.tags else ""
        date = note.created or note.updated or ""
        lines.append(f"  {date} — {note.title} ({path}){tags}")
    return "\n".join(lines)


@mcp.tool()
def kyp_project_context(project: str) -> str:
    """CALL THIS AT SESSION START. Returns the project's full context: Knowledge.md (ground truth), project notes, and recent session summaries. Use this to understand architecture, known bugs, past decisions, and what was done recently. This prevents hallucination and avoids repeating past work."""
    parts = []

    MAX_KNOWLEDGE_CHARS = 3000
    MAX_NOTE_PREVIEW_LINES = 6
    MAX_SESSION_CHARS = 400

    knowledge_path = f"{project}/Knowledge.md"
    knowledge = vault.read(knowledge_path)
    if knowledge:
        parts.append("=== PROJECT KNOWLEDGE ===")
        content = knowledge.content
        if len(content) > MAX_KNOWLEDGE_CHARS:
            parts.append(content[:MAX_KNOWLEDGE_CHARS])
            parts.append(f"\n... (truncated — use kyp_read(\"{knowledge_path}\", full=True) for complete content)")
        else:
            parts.append(content)
        parts.append("")

    project_notes = []
    for path, note in vault.index.notes.items():
        if path.startswith(f"{project}/") and "/Sessions/" not in path and path != knowledge_path:
            project_notes.append((path, note))

    if project_notes:
        parts.append("=== PROJECT NOTES ===")
        for path, note in sorted(project_notes):
            parts.append(f"\n--- {note.title} ({path}) ---")
            preview = note.content.strip().split("\n")
            parts.append("\n".join(preview[:MAX_NOTE_PREVIEW_LINES]))
            if len(preview) > MAX_NOTE_PREVIEW_LINES:
                parts.append(f"... (use kyp_read(\"{path}\", full=True) for complete content)")
        parts.append("")

    sessions = []
    for path, note in vault.index.notes.items():
        if path.startswith(f"{project}/Sessions/"):
            sessions.append((path, note))
    sessions.sort(key=lambda s: s[0], reverse=True)
    recent = sessions[:5]

    if recent:
        parts.append(f"=== RECENT SESSIONS ({len(recent)} of {len(sessions)}) ===")
        for path, note in recent:
            parts.append(f"\n--- {note.title} ---")
            content = note.content
            timeline_idx = content.find("## Timeline")
            if timeline_idx > 0:
                content = content[:timeline_idx].strip()
            if len(content) > MAX_SESSION_CHARS:
                parts.append(content[:MAX_SESSION_CHARS] + "...")
            else:
                parts.append(content)
        parts.append("")

    if not parts:
        return f"No context found for project '{project}'. Create a Knowledge.md to get started."

    return "\n".join(parts)
