# KYP-MEM — Claude Code Instructions

## What is KYP-MEM

KYP-MEM is a persistent knowledge base for AI agents. It has two pillars:

1. **Projects (Obsidian Pillar)** — Long-term structural memory. Markdown files with wikilinks, tags, backlinks, and folder structure. This is where ground truth lives: architecture, bugs, decisions, setup guides.

2. **Sessions (Claude-Mem Pillar)** — Semantic session memory. Auto-captured session logs with vector embeddings for semantic search. Every session records what was investigated, learned, completed, and what's next.

## Session Start Protocol

When starting work on any project that has kyp-mem configured:

1. Call `kyp_project_context("<ProjectName>")` to load the project's knowledge base and recent sessions.
2. Read the returned context carefully — it contains architecture, known bugs, past decisions, and recent session history.
3. Do NOT ask the user questions that are already answered in the project context.
4. If the project doesn't exist yet, offer to create it.

## During Work

### Search before you guess
- Before investigating a bug, search sessions: `kyp_session_search("error message or symptom")`
- Before making an architectural decision, check if it was already decided: `kyp_session_search("decision about X")`
- Use `kyp_search("keyword")` for full-text search across all project notes.

### Update knowledge as you go
When you discover something permanent, update the project's Knowledge.md:
- Fixed a bug → add under `## Bugs > ### Fixed`
- Found a new bug → add under `## Bugs > ### Known`
- Made a decision → add under `## Key Decisions`
- Learned something → add under `## Notes`

Use `kyp_write("Project/Knowledge.md", content, tags)` to update.

### Create notes for substantial topics
For deep-dives, API docs, or component guides, create separate notes:
- `kyp_write("Project/API.md", content, "api,reference")`
- Use `[[wikilinks]]` to connect notes together.

## Session Capture

Sessions are captured automatically by hooks when a Claude Code session ends. You do not need to manually create session notes. The hooks record:
- Files modified and created
- Commands investigated
- A structured summary with INVESTIGATED, LEARNED, COMPLETED, NEXT STEPS sections

## Architecture

- Python package with MCP server (`kyp_mem/server.py`)
- FastAPI web UI (`kyp_mem/ui.py`) on port 3333
- Vault engine (`kyp_mem/vault.py`) — markdown files with YAML frontmatter, wikilink parsing, full-text indexing
- Vector store (`kyp_mem/vector.py`) — ChromaDB for semantic session search
- Hooks (`kyp_mem/hooks.py`) — auto-capture Claude Code sessions
- Frontend (`kyp_mem/static/index.html`) — single-file HTML/CSS/JS with D3.js graph

## Dev Commands

```bash
# Run the web UI
python3 -m kyp_mem.cli ui

# Run MCP server (stdio)
python3 -m kyp_mem.cli serve

# Install hooks
python3 -m kyp_mem.cli install-hooks

# Diagnostics
python3 -m kyp_mem.cli doctor
```
