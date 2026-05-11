# KYP-MEM — Know Your Project Memory

**Headless Obsidian for AI agents.** A markdown knowledge base with wikilinks, backlinks, tags, related notes, and a neon web UI — all powered by an MCP server so Claude (or any AI) can read and write your project knowledge directly.

## Install

```bash
pip install kyp-mem
```

## Quick Start

```bash
# First-time setup — choose where your vault lives
kyp-mem init

# Open the web UI
kyp-mem ui

# Start MCP server (for Claude Code / AI agents)
kyp-mem serve
```

## Commands

| Command | Description |
|---------|-------------|
| `kyp-mem init` | Set up vault location |
| `kyp-mem ui` | Open web UI (localhost:3333) |
| `kyp-mem ui --port 4000` | Custom port |
| `kyp-mem serve` | Start MCP server (stdio) |
| `kyp-mem stats` | Print vault statistics |
| `kyp-mem tree` | Print vault tree |
| `kyp-mem --vault /path` | Override vault location |

## Connect to Claude Code

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "kyp-mem": {
      "command": "kyp-mem",
      "args": ["serve"]
    }
  }
}
```

Claude gets these tools: `kyp_list`, `kyp_read`, `kyp_write`, `kyp_delete`, `kyp_search`, `kyp_tags`, `kyp_related`, `kyp_recent`, `kyp_stats`.

## How Notes Work

Notes are standard markdown files with YAML frontmatter — same format as Obsidian:

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

**Features:**
- `[[Wikilinks]]` — automatically parsed and indexed
- **Backlinks** — see which notes reference the current one
- **Related notes** — ranked by shared tags, links, and folder proximity
- **Tags** — filterable metadata on every note
- **Full-text search** — across all notes
- **Graph view** — D3 force graph showing note connections

## Web UI

The web UI provides an Obsidian-like experience with:
- Collapsible folder tree sidebar
- Rendered markdown with syntax-highlighted code blocks
- Clickable wikilinks
- Backlinks and related notes panel
- Interactive graph view (toggleable)
- Full-text search with `Cmd+K`

## Architecture

```
~/.kyp-mem/
  config.json          # Vault path configuration
  vault/               # Your knowledge base
    Project A/
      Architecture.md
      Configuration.md
      Bugs.md
    Project B/
      ...
```

Zero database. Just markdown files + an in-memory index rebuilt on startup.

## License

MIT
