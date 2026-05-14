# KYP-MEM


> Claude that remembers your conversations AND understands your project.

KYP-MEM gives AI coding agents two-layer memory:

- **Session Memory(Episodic)** → remembers what happened across coding sessions

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




## How it works

KYP-MEM operates as a Model Context Protocol (MCP) server that runs silently in the background, integrating directly with Claude Code.

## Two-Layer Memory System

### 1. Episodic Memory

Every coding session is automatically captured:

- prompts
- commands
- files changed
- bugs investigated
- decisions made

Sessions are semantically searchable.

### 2. Project Intelligence

KYP-MEM maintains structured project knowledge:

- architecture
- APIs
- setup docs
- known issues
- linked concepts
- decision history

The agent continuously updates this knowledge as it learns.

1. **Vault Storage:** Your knowledge base and session logs are stored locally as Markdown files in your `~/.kyp-mem/vault` directory.
2. **Vector Database:** Session logs are embedded into a local ChromaDB vector database, enabling semantic search ("Find me the session where we debugged the database connection").
3. **Auto-Learning Hooks:** KYP-MEM hooks into Claude Code's execution lifecycle. It silently listens to prompts, file reads, edits, and terminal commands. When a session ends, it automatically generates a comprehensive summary and timeline using an LLM and saves it to your Vault.
4. **Agent Tooling:** Claude is equipped with 14 custom MCP tools to read, write, search, and navigate your project's knowledge graph using `[[wikilinks]]`.

## Installation

```bash
npm i kyp-mem

pip install kyp-mem (coming soon)
```

## Setup

Run the initialization commands to get started:

```bash
kyp-mem init              # Choose where to store your vault
kyp-mem setup-claude      # Register the MCP server with Claude Code
kyp-mem install-hooks     # Enable automatic session capture (Episodic Memory)
```

Restart Claude Code. The agent will automatically have access to the memory tools.

*(To enable globally for all projects run: `kyp-mem setup-claude --global` and `kyp-mem install-hooks --global`)*

## The Agent's Workflow

KYP-MEM embeds behavioral instructions directly into its tools. Without any prompting required from you, the agent will automatically:

1. **Load Context:** On session start, it loads the project's ground truth (`Knowledge.md`) and recent session summaries.
2. **Search Before Acting:** Before investigating bugs or making architectural decisions, it searches past episodic memory to avoid repeating work.
3. **Persist Knowledge:** After fixing a bug or making a decision, it uses its tools to update the project's knowledge base for future sessions.

## Web UI

Browse your knowledge graph, view session timelines, and see semantic relationships visually.

```bash
kyp-mem ui
```
*Opens at `localhost:3333`.*

## CLI Commands

| Command | Description |
|---------|-------------|
| `kyp-mem init` | First-time setup — choose vault location |
| `kyp-mem setup-claude` | Register MCP server with Claude Code |
| `kyp-mem install-hooks` | Enable automatic session capture |
| `kyp-mem serve` | Start MCP server (stdio, used by the agent) |
| `kyp-mem ui` | Open the local web UI |
| `kyp-mem stats` | Print vault statistics |
| `kyp-mem tree` | Print vault file tree |
| `kyp-mem doctor` | Check installation and configuration health |

## License

MIT
