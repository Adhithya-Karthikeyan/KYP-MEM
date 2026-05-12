# KYP-MEM — Know Your Project Memory

**Create memory for AI agents so they never hallucinate about your project.**

KYP-MEM builds a persistent, searchable knowledge base that Claude reads at every session start. Instead of guessing or asking you the same questions, Claude knows your architecture, remembers past bugs, recalls what it investigated last time, and picks up exactly where it left off.

Two pillars. One system.

---

## The Problem

Every time you start a new Claude session, it starts from zero. It doesn't know your project's architecture, doesn't remember the bug it fixed yesterday, and will confidently hallucinate details about your codebase. You end up repeating context, re-explaining decisions, and watching Claude re-investigate things it already solved.

## The Solution

KYP-MEM gives Claude two types of memory:

### Projects (Obsidian Pillar) — What Claude knows

Long-term structural memory. Markdown files with `[[wikilinks]]`, `#tags`, backlinks, and folder structure. This is ground truth — architecture decisions, API contracts, known bugs, setup guides. Claude reads this first to understand your project before doing anything.

```
MyProject/
├── Knowledge.md          # Architecture, bugs, decisions, notes
├── API.md                # API contracts and endpoints
├── Setup.md              # Environment and configuration guide
└── Sessions/             # (managed by the Sessions pillar)
```

### Sessions (Claude-Mem Pillar) — What Claude did

Semantic session memory. Every Claude Code session is automatically captured with structured logs — what was investigated, what was learned, what was completed, what's next. These are embedded into a **ChromaDB vector database** so Claude can semantically search past work, not just keyword match.

```
MyProject/Sessions/
├── 2026-05-12_143022.md  # "Fixed the auth bug, found rate limiter issue"
├── 2026-05-12_091544.md  # "Investigated flaky tests, root cause: race condition"
└── 2026-05-11_162301.md  # "Set up CI pipeline, decided on GitHub Actions"
```

---

## How It Works

```
┌─────────────────┐        ┌──────────────────┐        ┌─────────────────────┐
│   Claude Code   │─stdio─▶│   KYP-MEM MCP    │─read/─▶│   Vault (Markdown)  │
│   (any AI)      │◀───────│   Server         │ write  │   ~/.kyp-mem/vault/ │
└────────┬────────┘        └────────┬─────────┘        └─────────────────────┘
         │                          │
         │ hooks (auto)             │ embeddings
         ▼                          ▼
┌─────────────────┐        ┌──────────────────┐
│ Session Capture │───────▶│   ChromaDB        │
│ (PostToolUse +  │        │   Vector Store    │
│  Stop hooks)    │        │   (semantic search)│
└─────────────────┘        └──────────────────┘
```

**Session start:** Claude calls `kyp_project_context("MyProject")` → gets Knowledge.md + recent sessions → grounds itself before doing any work.

**During work:** Claude encounters a bug → calls `kyp_session_search("error message")` → finds a past session where it already investigated this → skips re-investigation and applies the known fix.

**After fixing:** Claude updates `Knowledge.md` with the bug fix → future sessions know about it automatically.

**Session end:** Hooks auto-capture everything Claude did → structured note with INVESTIGATED/LEARNED/COMPLETED/NEXT STEPS → embedded into ChromaDB for future semantic search.

---

## Install

```bash
npm install -g kyp-mem
```

Or run directly:

```bash
npx -y kyp-mem
```

## Setup

```bash
kyp-mem init              # Choose vault location
kyp-mem setup-claude      # Auto-configure Claude Code MCP
kyp-mem install-hooks     # Enable auto-learning from sessions
```

Restart Claude Code. KYP-MEM runs headlessly every session with 14 tools available.

### Manual Claude Code Config

```bash
claude mcp add -s user -e KYP_VAULT="$HOME/.kyp-mem/vault" kyp-mem -- npx -y kyp-mem serve
```

### Enable for All Projects

```bash
kyp-mem setup-claude --global
kyp-mem install-hooks --global
```

---

## What Claude Sees

KYP-MEM uses the **tool-description-as-instruction** pattern (same as claude-mem). The `____kyp_instructions` tool's description contains behavioral rules that Claude follows automatically:

1. **At session start** — Call `kyp_project_context()` to load knowledge + recent sessions
2. **Before investigating bugs** — Search past sessions first with `kyp_session_search()`
3. **Before making decisions** — Check if a prior session already decided this
4. **After fixing/learning** — Update `Knowledge.md` so future sessions know
5. **Never hallucinate** — If it's not in the knowledge base, say so

No user action needed. Claude reads tool descriptions automatically and follows the protocol.

---

## Web UI

```bash
kyp-mem ui
```

Opens at `localhost:3333` with two-pillar sidebar:

**Sessions panel (top):**
- Semantic search bar — vector search across all session logs
- Project-grouped dropdowns with session timestamps
- One-click session creation per project

**Projects panel (bottom):**
- File tree with folders, notes, inline tags
- Tag cloud with AND-filtering
- Quick switcher (`Cmd+O`)
- Full-text search (`Cmd+K`)

**Content area:**
- Rendered markdown with `[[wikilink]]` navigation
- Inline editing (`Cmd+S`)
- Session badge for session notes

**Right panel:**
- D3.js force-directed graph (resizable, zoomable, maximizable)
- Outline (heading TOC with click-to-scroll)
- Backlinks with surrounding context
- Related notes by links, tags, folder proximity
- Outgoing links and unlinked mentions

---

## Embeddings and Vector Search

Session notes are embedded into **ChromaDB** using its default embedding model. This enables semantic search — not just keyword matching, but meaning-based retrieval.

**How it works:**
- When a session note is written to the vault, it's automatically embedded and upserted into ChromaDB
- When a session note is deleted, it's removed from the vector store
- On server startup, all existing session notes are synced to ensure consistency
- `kyp_session_search(query)` performs a vector similarity search and returns the most relevant past sessions

**Why this matters:**
- Searching "authentication failing" finds sessions about "login bug" and "OAuth token expiry"
- Searching "deploy process" finds sessions about "CI pipeline setup" and "release workflow"
- Claude doesn't need exact keywords — it finds semantically related past work

The ChromaDB database is stored at `~/.kyp-mem/chroma/` alongside the vault.

---

## Auto-Learning (Session Capture)

```bash
kyp-mem install-hooks --global
```

Installs two Claude Code hooks:

- **PostToolUse hook** — captures file edits, writes, and shell commands in real-time (Node.js, fast)
- **Stop hook** — when the session ends, compiles all captured activity into a structured session note

Each session note follows a rigid format:

```markdown
# Session 2026-05-12_143022

**Project:** MyProject
**Actions:** 15 total, 12 substantive

## Summary
Fixed auth bug, refactored token refresh logic.

## INVESTIGATED
- grep for "token expired" across auth module
- read OAuth provider docs

## LEARNED
- Refresh tokens expire after 30 days, not 90

## COMPLETED
- Fixed token refresh in auth.py
- Added retry logic for expired tokens

## NEXT STEPS
- Add integration test for token refresh flow
- Monitor error rates after deploy
```

Sessions with fewer than 3 substantive actions are automatically skipped (no noise).

---

## MCP Tools (14 tools)

### Behavioral

| Tool | Description |
|------|-------------|
| `____kyp_instructions` | System instructions — tells Claude how and when to use every tool |
| `kyp_project_context` | **Session start** — loads Knowledge.md + project notes + recent sessions |

### Knowledge (Obsidian Pillar)

| Tool | Description |
|------|-------------|
| `kyp_list` | Browse folders and notes with inline tags |
| `kyp_read` | Brief summary by default; `full=True` for complete content |
| `kyp_write` | Create or update a note with tags, properties, and `[[wikilinks]]` |
| `kyp_delete` | Delete a note |
| `kyp_search` | Full-text keyword search with optional tag filter |
| `kyp_tags` | List all tags or filter notes by tag |
| `kyp_related` | Find related notes by links, tags, folder proximity |
| `kyp_recent` | Recently modified notes |
| `kyp_stats` | Vault statistics |

### Sessions (Claude-Mem Pillar)

| Tool | Description |
|------|-------------|
| `kyp_session_search` | **Semantic vector search** across all session logs |
| `kyp_session_create` | Manually create a structured session note |
| `kyp_sessions` | List sessions by project, most recent first |

---

## Project Structure

```
~/.kyp-mem/
├── config.json           # vault path configuration
├── chroma/               # ChromaDB vector database
└── vault/
    ├── ProjectA/
    │   ├── Knowledge.md  # ground truth: architecture, bugs, decisions
    │   ├── API.md        # API documentation
    │   ├── Setup.md      # environment guide
    │   └── Sessions/
    │       ├── 2026-05-12_143022.md
    │       └── 2026-05-11_091544.md
    ├── ProjectB/
    │   ├── Knowledge.md
    │   └── Sessions/
    └── ...
```

## Note Format

```markdown
---
tags: [project, trading, config]
created: 2026-05-12
---

# Configuration

Settings are in `HedgeConfig`. See [[Risk Management]] for safety checks.
```

`[[Wikilinks]]` are parsed, indexed, and resolved into navigable backlinks automatically.

---

## Adding KYP-MEM to Your Project

To make Claude automatically use KYP-MEM in any project, add a `CLAUDE.md` to the project root:

```markdown
# Project Memory — KYP-MEM

## Session Start (MANDATORY)
At the start of every session, call `kyp_project_context("PROJECT_NAME")` to load:
- Project knowledge base (architecture, bugs, decisions)
- Recent session history (what was done, what's next)

## During Work
- **Before investigating bugs:** `kyp_session_search("error or symptom")`
- **Before decisions:** `kyp_session_search("topic")`
- **After fixing bugs:** Update Knowledge.md via `kyp_write`
- **After decisions:** Add to Key Decisions section in Knowledge.md

## Rules
- Never hallucinate project details — check the knowledge base first.
- Use [[wikilinks]] to connect notes.
- Sessions are auto-captured — no manual logging needed.
```

A template is available at `templates/CLAUDE.md.template`.

---

## Commands

| Command | What it does |
|---------|-------------|
| `kyp-mem init` | First-time setup — choose vault location |
| `kyp-mem setup-claude` | Register MCP server with Claude Code |
| `kyp-mem setup-claude --global` | Configure globally (all projects) |
| `kyp-mem install-hooks` | Enable auto-learning from sessions |
| `kyp-mem install-hooks --remove` | Remove auto-learning hooks |
| `kyp-mem serve` | Start MCP server (used by Claude, not you) |
| `kyp-mem ui` | Open web UI at localhost:3333 |
| `kyp-mem stats` | Print vault statistics |
| `kyp-mem tree` | Print vault tree |
| `kyp-mem doctor` | Check installation health |

---

## Architecture

```
kyp_mem/
├── server.py       # MCP server — 14 tools over stdio
├── vault.py        # Vault engine — markdown, YAML frontmatter, wikilinks, indexing
├── vector.py       # ChromaDB vector store — session embeddings and semantic search
├── ui.py           # FastAPI web UI — REST API + static HTML
├── hooks.py        # Session auto-capture — PostToolUse + Stop hooks
├── config.py       # Configuration management
├── cli.py          # CLI entry point
└── static/
    └── index.html  # Single-file frontend — HTML/CSS/JS with D3.js
```

**Tech stack:** Python, FastMCP, FastAPI, ChromaDB, D3.js, Marked.js

---

## How KYP-MEM Compares to Claude-Mem

| Feature | claude-mem | KYP-MEM |
|---------|-----------|---------|
| Memory type | Observations + memories | Projects (structured) + Sessions (semantic) |
| Storage | SQLite | Markdown files + ChromaDB |
| Search | Vector search | Full-text (keywords) + Vector search (sessions) |
| Structure | Flat observations | Folder hierarchy with `[[wikilinks]]` and backlinks |
| Knowledge graph | No | Yes — wikilinks, backlinks, related notes, D3 graph |
| Project isolation | Tags | Folder-based with per-project Knowledge.md |
| Web UI | No | Yes — two-pillar sidebar, graph, search, editing |
| Auto-capture | Observation hooks | Session hooks (INVESTIGATED/LEARNED/COMPLETED/NEXT STEPS) |
| Instruction pattern | `____IMPORTANT` tool | `____kyp_instructions` tool |

KYP-MEM is designed for **project-scoped structural knowledge** with semantic session recall. Claude-mem is designed for **cross-project observation capture**. They complement each other.

---

## License

MIT
