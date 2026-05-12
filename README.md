# KYP-MEM — Know Your Project Memory

**Persistent memory for AI coding agents.** Your AI agent forgets everything between sessions. KYP-MEM fixes that.

KYP-MEM is an MCP server that gives your AI agent a knowledge base and session memory. It remembers your architecture, past bugs, decisions, and what happened last session — so you never repeat yourself.

---

## How It Works

KYP-MEM gives your agent two types of memory:

### Knowledge Base — long-term project memory

Structured markdown notes organized by project. Architecture docs, API references, known bugs, key decisions, setup guides. The agent reads these before doing any work and updates them as it learns.

```
MyProject/
├── Knowledge.md      # Architecture, bugs, decisions, notes
├── API.md            # Endpoints and contracts
├── Setup.md          # Environment setup guide
└── Sessions/
```

### Session Memory — what happened each session

Every coding session is automatically captured — files changed, commands run, prompts used. These notes are embedded into a vector database for semantic search, so the agent can find past work by meaning, not just keywords.

```
MyProject/Sessions/
├── 2026-05-12_143022.md  # "Fixed auth bug, found rate limiter issue"
├── 2026-05-12_091544.md  # "Investigated flaky tests — race condition"
└── 2026-05-11_162301.md  # "Set up CI, decided on GitHub Actions"
```

### The Loop

```
Session Start
  → Agent loads project knowledge + recent sessions
  → Agent is grounded: knows architecture, past bugs, last session's next steps

During Work
  → Agent hits a bug → searches session memory → finds it was already investigated
  → Agent makes a decision → updates Knowledge.md so future sessions know

Session End
  → Hooks auto-capture everything into a structured session note
  → Note is embedded into the vector store for future semantic search
```

---

## Install

```bash
pip install kyp-mem
```

## Setup

```bash
kyp-mem init              # Choose where to store your vault
kyp-mem setup-claude      # Register MCP server with Claude Code
kyp-mem install-hooks     # Enable automatic session capture
```

Restart Claude Code. KYP-MEM runs automatically with 14 tools available to the agent.

### Quick Setup (one command)

```bash
claude mcp add -s user -e KYP_VAULT="$HOME/.kyp-mem/vault" kyp-mem -- kyp-mem serve
```

### Enable for All Projects

```bash
kyp-mem setup-claude --global
kyp-mem install-hooks --global
```

---

## What the Agent Does Automatically

KYP-MEM embeds behavioral instructions directly into tool descriptions. The agent follows these rules without any user action:

1. **Session start** — loads project knowledge + recent session history
2. **Before investigating bugs** — searches session memory first to avoid duplicate work
3. **Before making decisions** — checks if a prior session already decided this
4. **After fixing or learning something** — updates Knowledge.md for future sessions
5. **Never hallucinates** — if it's not in the knowledge base, it says so

No prompting needed. The agent reads the tool descriptions and follows the protocol.

---

## Automatic Session Capture

```bash
kyp-mem install-hooks
```

Installs three Claude Code hooks:

- **UserPromptSubmit** — captures every prompt you send to the agent
- **PostToolUse** — captures file edits, writes, and shell commands in real-time
- **Stop** — when the session ends, compiles all activity into a structured note

Each session note looks like this:

```markdown
# Session 2026-05-12_143022

**Project:** MyProject
**Actions:** 15 total, 12 substantive

## Summary
Fixed auth bug, refactored token refresh logic.

## PROMPTS
### 1. [14:25:01]
> fix the token refresh bug in auth.py

### 2. [14:30:22]
> also add retry logic for expired tokens

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
```

Sessions with fewer than 3 substantive actions are automatically skipped.

---

## Semantic Search

Session notes are embedded into a vector database (ChromaDB). This enables search by meaning:

- Searching **"authentication failing"** finds sessions about "login bug" and "OAuth token expiry"
- Searching **"deploy process"** finds sessions about "CI pipeline setup" and "release workflow"

The agent doesn't need exact keywords — it finds semantically related past work.

---

## Web UI

```bash
kyp-mem ui
```

Opens at `localhost:3333` with two panels:

**Session Memory** — semantic search across all sessions, grouped by project with timestamps

**Knowledge Base** — file tree with folders, notes, tags. Full-text search, tag filtering, quick switcher (`Cmd+O`)

**Editor** — rendered markdown with `[[wikilink]]` navigation, inline editing

**Graph** — knowledge graph showing connections between notes, backlinks, and related content

---

## MCP Tools (14 total)

### Agent Behavior

| Tool | Purpose |
|------|---------|
| `____kyp_instructions` | Embeds behavioral rules the agent follows automatically |
| `kyp_project_context` | Loads project knowledge + recent sessions at session start |

### Knowledge Base

| Tool | Purpose |
|------|---------|
| `kyp_list` | Browse folders and notes |
| `kyp_read` | Read a note (summary by default, `full=True` for complete) |
| `kyp_write` | Create or update a note with tags and `[[wikilinks]]` |
| `kyp_delete` | Delete a note |
| `kyp_search` | Full-text search with optional tag filter |
| `kyp_tags` | List all tags or filter notes by tag |
| `kyp_related` | Find related notes by links, tags, proximity |
| `kyp_recent` | Recently modified notes |
| `kyp_stats` | Vault statistics |

### Session Memory

| Tool | Purpose |
|------|---------|
| `kyp_session_search` | Semantic search across all session logs |
| `kyp_session_create` | Manually create a session note |
| `kyp_sessions` | List sessions by project |

---

## Adding to Your Project

Add a `CLAUDE.md` to any project root:

```markdown
# Project Memory

## Session Start (MANDATORY)
Call `kyp_project_context("PROJECT_NAME")` at the start of every session to load:
- Project knowledge base (architecture, bugs, decisions)
- Recent session history (what was done, what's next)

## During Work
- Before investigating bugs: `kyp_session_search("error or symptom")`
- Before making decisions: `kyp_session_search("topic")`
- After fixing bugs: update Knowledge.md via `kyp_write`
- After decisions: add to Key Decisions in Knowledge.md

## Rules
- Never hallucinate project details — check the knowledge base first.
- Use [[wikilinks]] to connect related notes.
- Sessions are captured automatically — no manual logging needed.
```

A template is available at `templates/CLAUDE.md.template`.

---

## Vault Structure

```
~/.kyp-mem/
├── config.json           # Vault path configuration
├── chroma/               # Vector database for semantic search
└── vault/
    ├── ProjectA/
    │   ├── Knowledge.md  # Ground truth: architecture, bugs, decisions
    │   ├── API.md
    │   └── Sessions/
    │       ├── 2026-05-12_143022.md
    │       └── 2026-05-11_091544.md
    ├── ProjectB/
    │   ├── Knowledge.md
    │   └── Sessions/
    └── ...
```

Notes use YAML frontmatter for tags and `[[wikilinks]]` for cross-references:

```markdown
---
tags: [trading, config]
created: 2026-05-12
---
# Configuration
Settings are in `HedgeConfig`. See [[Risk Management]] for limits.
```

---

## CLI Commands

| Command | What it does |
|---------|-------------|
| `kyp-mem init` | First-time setup — choose vault location |
| `kyp-mem setup-claude` | Register MCP server with Claude Code |
| `kyp-mem setup-claude --global` | Register globally (all projects) |
| `kyp-mem install-hooks` | Enable automatic session capture |
| `kyp-mem install-hooks --remove` | Remove session capture hooks |
| `kyp-mem serve` | Start MCP server (stdio, used by the agent) |
| `kyp-mem ui` | Open web UI at localhost:3333 |
| `kyp-mem stats` | Print vault statistics |
| `kyp-mem tree` | Print vault tree |
| `kyp-mem doctor` | Check installation health |

---

## License

MIT
