# KYP-MEM


> Claude that remembers your conversations AND understands your project.

KYP-MEM gives AI coding agents two-layer memory:

- **Session Memory (Episodic)** → remembers what happened across coding sessions

- **Project Intelligence** → understands architecture, decisions, docs, and relationships

Your AI agent stops starting from zero every day.

## Example

User:

> "Why did we move from REST to Kafka?"

Claude:

> "In the May 12 session, we found the REST pipeline couldn't handle peak trading volume.
> We decided to migrate to Kafka for async processing.
> See [[Architecture Decisions]] and [[Event Pipeline]]."

By intercepting the prompt, KYP-MEM automatically provided the agent with:
- The vectorized semantic search results of past session logs.
- The relevant markdown files from the project knowledge base.

## How It Works

KYP-MEM operates as a Model Context Protocol (MCP) server that runs silently in the background, integrating directly with Claude Code.

### 1. Episodic Memory (Sessions)

Every coding session is automatically captured with full context:

- User prompts (what was asked)
- File reads with content (what was found)
- File edits with diffs (what changed and why)
- Command outputs (what happened)

At session end, Claude Sonnet synthesizes raw activity into a structured summary with **Summary**, **Investigated**, **Learned**, **Completed**, and **Next Steps** sections. Sessions are semantically searchable via ChromaDB vector embeddings.

### 2. Project Intelligence (Vault)

KYP-MEM maintains structured project knowledge as Markdown files with `[[wikilinks]]`:

- Architecture docs, API references, setup guides
- Known issues, decision history, linked concepts

The agent searches this on-demand via `kyp_search` when it needs project context.

### How It All Connects

1. **Session Start:** Recent session summaries are injected automatically — the agent knows what happened last time.
2. **During Work:** Hooks capture tool activity (reads, edits, commands) with actual content, not just file names.
3. **Session End:** Sonnet synthesizes a rich, semantic summary and saves it to the vault + vector DB.
4. **Future Sessions:** The agent can search past sessions semantically or look up project knowledge on demand.

## Installation

```bash
npm install -g kyp-mem
```

That's it. The postinstall script automatically:

1. Installs the Python package
2. Creates the default vault at `~/.kyp-mem/vault`
3. Registers the MCP server with Claude Code
4. Installs session capture hooks

Restart Claude Code and you're ready to go.

### Requirements

- Node.js 18+
- Python 3.10+
- Claude Code CLI
- Anthropic API key (for session summarization with Sonnet)

### Custom Vault Path

If you want to store your vault somewhere other than `~/.kyp-mem/vault`:

```bash
kyp-mem init    # Interactive prompt to choose vault location
```

## The Agent's Workflow

KYP-MEM embeds behavioral instructions directly into its tools. Without any prompting from you, the agent will automatically:

1. **Load Context:** On session start, it loads recent session summaries so it knows what happened last time.
2. **Search Before Acting:** Before investigating bugs or making decisions, it searches past sessions to avoid repeating work.
3. **Persist Knowledge:** After fixing a bug or making a decision, it updates the project's knowledge base for future sessions.

## Web UI

Browse your knowledge graph, view session timelines, and see semantic relationships visually.

```bash
kyp-mem ui
```
*Opens at `localhost:3333`.*

## CLI Commands

| Command | Description |
|---------|-------------|
| `kyp-mem init` | Choose vault location (default: `~/.kyp-mem/vault`) |
| `kyp-mem setup-claude` | Register MCP server with Claude Code |
| `kyp-mem install-hooks` | Enable automatic session capture |
| `kyp-mem serve` | Start MCP server (stdio, used by the agent) |
| `kyp-mem ui` | Open the local web UI |
| `kyp-mem stats` | Print vault statistics |
| `kyp-mem tree` | Print vault file tree |
| `kyp-mem config` | View or set configuration (e.g. `kyp-mem config session_model`) |
| `kyp-mem doctor` | Check installation and configuration health |
| `kyp-mem uninstall` | Remove hooks and MCP server from Claude Code |

## Uninstall

```bash
# Remove from Claude Code (keeps your vault data)
kyp-mem uninstall

# Remove from Claude Code AND delete all data
kyp-mem uninstall --purge

# Remove the npm package
npm uninstall -g kyp-mem
```

## License

MIT
