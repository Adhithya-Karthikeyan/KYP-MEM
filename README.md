# KYP-MEM — Know Your Project Memory

**Persistent memory for AI coding agents.** Claude forgets everything between sessions. KYP-MEM fixes that.

KYP-MEM is a knowledge base that your AI agent reads at every session start. It remembers your architecture, past bugs, decisions, and what happened last session — so you never repeat yourself.

---

## The Problem

Every new Claude session starts from zero. It doesn't know your project structure, doesn't remember the bug it fixed yesterday, and will confidently make up details about your codebase. You end up re-explaining the same things every time.

## How KYP-MEM Solves It

KYP-MEM gives your agent two kinds of memory:

### Project Knowledge — what the agent knows

Structured markdown notes organized by project. Architecture docs, API contracts, known bugs, key decisions, setup guides. The agent reads this first before doing any work.

```
MyProject/
├── Knowledge.md          # Architecture, bugs, decisions, notes
├── API.md                # API contracts and endpoints
├── Setup.md              # Environment and configuration guide
└── Sessions/
```

### Session History — what the agent did

Every coding session is automatically captured — what was investigated, learned, completed, and what's next. These logs are embedded into a vector database, so the agent can search past work by meaning, not just keywords.

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
│   (AI Agent)    │◀───────│   Server         │ write  │   ~/.kyp-mem/vault/ │
└────────┬────────┘        └────────┬─────────┘        └─────────────────────┘
         │                          │
         │ hooks (auto)             │ embeddings
         ▼                          ▼
┌─────────────────┐        ┌──────────────────┐
│ Session Capture │───────▶│   Vector Store    │
│ (auto-hooks)    │        │   (semantic search)│
└─────────────────┘        └──────────────────┘
```

**Session start:** Agent calls `kyp_project_context("MyProject")` → gets project knowledge + recent sessions → grounds itself before doing any work.

**During work:** Agent encounters a bug → calls `kyp_session_search("error message")` → finds a past session where it already investigated this → skips re-investigation and applies the known fix.

**After fixing:** Agent updates `Knowledge.md` with the fix → future sessions know about it automatically.

**Session end:** Hooks auto-capture everything the agent did → structured note → embedded into the vector store for future semantic search.

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

Restart Claude Code. KYP-MEM runs automatically every session with 14 tools available.

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

## Agent Behavior

KYP-MEM uses tool descriptions to guide agent behavior automatically. The `____kyp_instructions` tool embeds behavioral rules that the agent follows without any user action:

1. **At session start** — Load project knowledge + recent sessions
2. **Before investigating bugs** — Search past sessions first
3. **Before making decisions** — Check if a prior session already decided this
4. **After fixing/learning** — Update Knowledge.md so future sessions know
5. **Never hallucinate** — If it's not in the knowledge base, say so

No user action needed. The agent reads tool descriptions and follows the protocol.

---

## Web UI

```bash
kyp-mem ui
```

Opens at `localhost:3333` with a two-panel sidebar:

**Sessions panel (top):**
- Semantic search bar — find past sessions by meaning
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

**Right panel:**
- Knowledge graph (D3.js force-directed, resizable, zoomable)
- Outline (heading TOC with click-to-scroll)
- Backlinks with surrounding context
- Related notes by links, tags, folder proximity

---

## Semantic Search

Session notes are embedded into a vector database using ChromaDB. This enables search by meaning — not just keyword matching.

- Searching "authentication failing" finds sessions about "login bug" and "OAuth token expiry"
- Searching "deploy process" finds sessions about "CI pipeline setup" and "release workflow"
- The agent doesn't need exact keywords — it finds semantically related past work

The vector database is stored at `~/.kyp-mem/chroma/`.

---

## Auto-Learning

```bash
kyp-mem install-hooks --global
```

Installs two Claude Code hooks that run automatically:

- **PostToolUse hook** — captures file edits, writes, and shell commands in real-time
- **Stop hook** — when the session ends, compiles all activity into a structured session note

Each session note follows this format:

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

Sessions with fewer than 3 substantive actions are automatically skipped.

---

## MCP Tools (14 total)

### Agent Instructions

| Tool | Description |
|------|-------------|
| `____kyp_instructions` | Behavioral rules — tells the agent how and when to use every tool |
| `kyp_project_context` | Load project knowledge + recent sessions at session start |

### Project Knowledge

| Tool | Description |
|------|-------------|
| `kyp_list` | Browse folders and notes with inline tags |
| `kyp_read` | Read a note (brief summary by default, `full=True` for complete) |
| `kyp_write` | Create or update a note with tags and `[[wikilinks]]` |
| `kyp_delete` | Delete a note |
| `kyp_search` | Full-text keyword search with optional tag filter |
| `kyp_tags` | List all tags or filter notes by tag |
| `kyp_related` | Find related notes by links, tags, folder proximity |
| `kyp_recent` | Recently modified notes |
| `kyp_stats` | Vault statistics |

### Session History

| Tool | Description |
|------|-------------|
| `kyp_session_search` | Semantic vector search across all session logs |
| `kyp_session_create` | Manually create a structured session note |
| `kyp_sessions` | List sessions by project, most recent first |

---

## Adding KYP-MEM to Your Project

Add a `CLAUDE.md` to any project root so the agent uses KYP-MEM automatically:

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

## Vault Structure

```
~/.kyp-mem/
├── config.json           # vault path configuration
├── chroma/               # vector database
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

## Commands

| Command | What it does |
|---------|-------------|
| `kyp-mem init` | First-time setup — choose vault location |
| `kyp-mem setup-claude` | Register MCP server with Claude Code |
| `kyp-mem setup-claude --global` | Configure globally (all projects) |
| `kyp-mem install-hooks` | Enable auto-learning from sessions |
| `kyp-mem install-hooks --remove` | Remove auto-learning hooks |
| `kyp-mem serve` | Start MCP server (used by the agent, not you) |
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
├── vector.py       # Vector store — session embeddings and semantic search
├── ui.py           # FastAPI web UI — REST API + static HTML
├── hooks.py        # Session auto-capture hooks
├── config.py       # Configuration management
├── cli.py          # CLI entry point
└── static/
    └── index.html  # Single-file frontend — HTML/CSS/JS with D3.js
```

**Tech stack:** Python, FastMCP, FastAPI, ChromaDB, D3.js

---

## License

MIT
