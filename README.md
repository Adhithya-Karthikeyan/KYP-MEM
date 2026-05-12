# KYP-MEM — Know Your Project Memory

**Persistent knowledge base for AI agents.** Markdown vault with wikilinks, backlinks, tags, graph navigation, and auto-learning — all powered by an MCP server so Claude (or any AI) can read and write project knowledge across sessions.

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
kyp-mem init            # Choose vault location
kyp-mem setup-claude    # Auto-configure Claude Code MCP
kyp-mem install-hooks   # Enable auto-learning from sessions
```

Restart Claude Code. Done — kyp-mem runs headlessly every session with 9 tools available.

## Auto-Learning

KYP-MEM can automatically capture what happens in every Claude Code session:

```bash
kyp-mem install-hooks --global
```

This installs two hooks:
- **PostToolUse** — captures file edits, writes, and commands (pure Node, fast)
- **Stop** — compiles session activity into a vault note under `Sessions/`

Sessions with fewer than 3 substantive actions are automatically skipped.

## Web UI

```bash
kyp-mem ui
```

Opens at `localhost:3333` with:
- Quick switcher (`Cmd+O`) — fuzzy jump to any note
- Full-text search (`Cmd+K`)
- Tag filtering — clickable tag cloud, AND-filter
- Outline panel — heading TOC with click-to-scroll
- Backlink context — shows the surrounding line
- Unlinked mentions — finds references without `[[wikilinks]]`
- Inline editing — edit notes directly in the browser (`Cmd+S`)
- Local graph view — D3 force-directed graph of connections
- Resizable panels, collapsible tree, rendered markdown

## How It Works

```
┌─────────────┐          ┌─────────────┐          ┌──────────────┐
│  Claude Code │──stdio──▶│  kyp-mem    │──read/──▶│  ~/.kyp-mem/ │
│  (any AI)    │◀─────────│  MCP server │  write   │  vault/      │
└─────────────┘          └─────────────┘          │    *.md files │
                                                   └──────────────┘
```

- **Headless by default** — MCP server over stdio, no GUI needed
- **Markdown on disk** — plain `.md` files with YAML frontmatter, no database
- **In-memory index** — wikilinks, backlinks, tags, word-level search index
- **Lightweight reads** — brief mode by default (~100 tokens), full content opt-in
- **Graph navigation** — follow `[[links]]` instead of searching broadly

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

## MCP Tools (9 tools)

| Tool | Description |
|------|-------------|
| `kyp_list` | Browse folders and notes with inline tags |
| `kyp_read` | Brief summary by default; `full=True` for complete content |
| `kyp_write` | Create or update a note with tags and properties |
| `kyp_delete` | Delete a note |
| `kyp_search` | Full-text search with optional tag filter |
| `kyp_tags` | List all tags or filter notes by tag |
| `kyp_related` | Find related notes by links, tags, folder proximity |
| `kyp_recent` | Recently modified notes |
| `kyp_stats` | Vault statistics |

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

## Manual Claude Code Config

```bash
claude mcp add -s user -e KYP_VAULT="$HOME/.kyp-mem/vault" kyp-mem -- npx -y kyp-mem serve
```

## Architecture

```
~/.kyp-mem/
├── config.json       # vault path
├── sessions/         # auto-learning session logs
└── vault/
    ├── Project A/
    │   ├── Architecture.md
    │   └── Bugs.md
    ├── Sessions/     # auto-captured session notes
    └── ...
```

## License

MIT
