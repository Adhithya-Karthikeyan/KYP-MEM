# KYP-MEM — Know Your Project Memory

**Headless knowledge base for AI agents.** Markdown notes with wikilinks, backlinks, tags, related notes, and a neon web UI — all powered by an MCP server so Claude (or any AI) can read and write your project knowledge directly.

## Install

```bash
pip install kyp-mem
```

## Setup (3 commands)

```bash
# 1. Choose where your vault (knowledge base) lives
kyp-mem init

# 2. Connect to Claude Code — auto-configures MCP
kyp-mem setup-claude

# 3. Restart Claude Code — done!
#    kyp-mem now runs headlessly every session.
#    Claude can read/write/search your knowledge base.
```

That's it. Claude now has `kyp_read`, `kyp_write`, `kyp_search`, and 7 other tools available in every session.

## Optional: Web UI

```bash
kyp-mem ui
```

Opens a rich interface at `localhost:3333` with:
- Collapsible folder tree
- Rendered markdown with syntax highlighting
- Clickable `[[wikilinks]]`
- Backlinks and related notes panel
- Interactive D3 graph view (toggleable)
- Full-text search (`Cmd+K`)
- Draggable resizable panels

## How It Works

```
┌─────────────┐          ┌─────────────┐          ┌──────────────┐
│  Claude Code │──stdio──▶│  kyp-mem    │──read/──▶│  ~/.kyp-mem/ │
│  (any AI)    │◀─────────│  MCP server │  write   │  vault/      │
└─────────────┘          └─────────────┘          │    *.md files │
                                                   └──────────────┘
```

- **Headless by default** — runs as an MCP server (stdio), no GUI needed
- **Markdown files on disk** — same format as Obsidian, no database
- **In-memory index** — links, backlinks, tags, search, similarity scoring
- **Web UI optional** — `kyp-mem ui` when you want to browse visually

## Commands

| Command | What it does |
|---------|-------------|
| `kyp-mem init` | First-time setup — choose vault location |
| `kyp-mem setup-claude` | Auto-configure Claude Code MCP settings |
| `kyp-mem setup-claude --global` | Configure globally (all projects) |
| `kyp-mem serve` | Start MCP server (used by Claude, not you) |
| `kyp-mem ui` | Open web UI at localhost:3333 |
| `kyp-mem stats` | Print vault statistics |
| `kyp-mem tree` | Print vault tree |
| `kyp-mem doctor` | Check installation health |

## MCP Tools (what Claude gets)

| Tool | Description |
|------|-------------|
| `kyp_list` | Browse vault folders and notes |
| `kyp_read` | Read a note — content + tags + backlinks + related |
| `kyp_write` | Create or update a note with tags and properties |
| `kyp_delete` | Delete a note |
| `kyp_search` | Full-text search across all notes |
| `kyp_tags` | List all tags or filter notes by tag |
| `kyp_related` | Find related notes by links, tags, proximity |
| `kyp_recent` | Recently modified notes |
| `kyp_stats` | Vault statistics |

## Note Format

Standard markdown with YAML frontmatter — same as Obsidian:

```markdown
---
tags: [project, trading, config]
source: config.py
created: 2026-05-12
updated: 2026-05-12
---

# Configuration

Settings are defined in `HedgeConfig`. See [[Risk Management]] for safety checks.
```

`[[Wikilinks]]` are automatically parsed, indexed, and turned into navigable backlinks.

## Manual Claude Code Config

If you prefer to configure manually instead of using `setup-claude`:

```json
{
  "mcpServers": {
    "kyp-mem": {
      "command": "kyp-mem",
      "args": ["serve"],
      "env": {
        "KYP_VAULT": "~/.kyp-mem/vault"
      }
    }
  }
}
```

Add to `~/.claude/settings.json` (global) or `.claude/settings.json` (per-project).

## Architecture

```
~/.kyp-mem/
├── config.json       # vault path + settings
└── vault/            # your knowledge base
    ├── Project A/
    │   ├── Architecture.md
    │   ├── Configuration.md
    │   └── Bugs.md
    └── Project B/
        └── ...
```

## Publishing to PyPI

```bash
pip install build twine
python3 -m build
twine upload dist/*
```

## License

MIT
