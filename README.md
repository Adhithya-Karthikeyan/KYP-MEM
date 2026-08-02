# KYP-MEM


> Claude Code & Kimi CLI that remember your conversations AND understand your project.

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

KYP-MEM operates as a Model Context Protocol (MCP) server that runs silently in the background, integrating directly with Claude Code and Kimi CLI.

### 1. Episodic Memory (Sessions)

Every coding session is automatically captured with full context:

- User prompts (what was asked)
- File reads with content (what was found)
- File edits with diffs (what changed and why)
- Command outputs (what happened)

At session end, Claude Sonnet synthesizes raw activity into a structured summary with **Summary**, **Investigated**, **Learned**, **Completed**, and **Next Steps** sections. Sessions are semantically searchable via local vector embeddings (a single SQLite file — no database server, no heavyweight dependencies).

### 2. Project Intelligence (Vault)

KYP-MEM maintains structured project knowledge as Markdown files with `[[wikilinks]]`:

- Architecture docs, API references, setup guides
- Known issues, decision history, linked concepts

The agent searches this on-demand via `kyp_search` when it needs project context.

### 3. Hybrid Retrieval

`kyp_search` runs two indexes over **every** note and fuses them with Reciprocal
Rank Fusion:

- **BM25 keyword search** — finds exact identifiers like `_prune_stale_logs`,
  error strings, and file names. Understands `snake_case` and `camelCase`, so
  "content hash" matches `_content_hash`.
- **Semantic search** — finds meaning. "Why does disk usage keep growing?" finds
  the note that explains it without sharing a single keyword.

Notes are embedded per markdown section rather than whole, so results point at
the exact heading that answered the query instead of a whole document.

Both indexes must clear a relevance floor. When nothing genuinely matches,
search returns **nothing** — an honest empty answer beats a confident wrong one.

### How It All Connects

1. **Session Start:** The project objective and one-line cues from recent sessions are injected automatically — the agent knows what happened last time without re-reading full summaries (those stay searchable in the vault).
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
5. Does the same for Kimi CLI, if it is installed on the machine

Restart Claude Code (or start a new Kimi CLI session) and you're ready to go.

### Kimi CLI

To set up Kimi CLI manually (or reinstall it later):

```bash
kyp-mem setup-kimi --global     # register the MCP server in ~/.kimi-code/mcp.json
kyp-mem install-kimi-hooks      # install session capture hooks in ~/.kimi-code/config.toml
```

Everything works the same as with Claude Code, with one difference: Kimi CLI
renders hook output as a visible block in its chat UI, so the Kimi hooks are
**fully silent** — they only capture (prompts, tool activity, session
summaries). Memory reaches the agent through the MCP tools instead:
`kyp_project_context` loads the project objective and recent sessions at
session start, and `kyp_search` / `kyp_session_search` answer questions on
demand. Nothing is ever printed into your chat.

### Requirements

- Node.js 18+
- Python 3.10+ — or [uv](https://docs.astral.sh/uv/), which fetches its own.
  When uv is installed, kyp-mem uses it to provision its Python environment
  (~100x faster than pip).
- Claude Code and/or Kimi CLI — the agent(s) KYP-MEM integrates with.
- Claude Code CLI — session summarization shells out to it and reuses your
  existing Claude Code login, so no separate API key is needed. Without it,
  sessions still save using a built-in structured fallback.

### Install size, and how to shrink it

The npm install provisions everything (~145 MB): MCP server, web UI, and
semantic search with a local ONNX embedding model — no torch, no database
server. PyPI users can pick a tier:

```bash
pip install kyp-mem                # ~30 MB — MCP server + BM25 keyword search
pip install 'kyp-mem[vector]'      # + semantic search (ONNX all-MiniLM-L6-v2)
pip install 'kyp-mem[vector-lite]' # + semantic search, numpy-only static
                                   #   embeddings (smaller, measurably weaker)
pip install 'kyp-mem[ui]'          # + local web UI
```

Without the `[vector]` extra, everything still works — search degrades to
keyword-only (BM25) and says so once on stderr. `KYP_MEM_LITE=1 npm install`
gets the tiny base through npm too.

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
| `kyp-mem setup-kimi` | Register MCP server with Kimi CLI (`--global` for user level, `--remove` to undo) |
| `kyp-mem install-kimi-hooks` | Enable automatic session capture for Kimi CLI (`--remove` to undo) |
| `kyp-mem serve` | Start MCP server (stdio, used by the agent) |
| `kyp-mem ui` | Open the local web UI |
| `kyp-mem stats` | Print vault statistics |
| `kyp-mem tree` | Print vault file tree |
| `kyp-mem config` | View or set configuration (e.g. `kyp-mem config session_model`) |
| `kyp-mem objective <project> [text]` | Read or set a project's objective (injected at every session start) |
| `kyp-mem doctor` | Check installation, configuration, and index health |
| `kyp-mem doctor --deep` | Also exercise the semantic index read/write path |
| `kyp-mem reindex` | Rebuild the semantic index from your markdown notes |
| `kyp-mem compact` | Reclaim disk used by the semantic index |
| `kyp-mem uninstall` | Remove hooks and MCP server from Claude Code |

## Index Maintenance

Your markdown notes are always the source of truth. The semantic index is
derived and can be rebuilt from them at any time, so every command here is safe.

The index is one SQLite file that reclaims space on delete; `kyp-mem compact`
re-embeds from your notes, vacuums the database, and — if you upgraded from a
pre-1.2 install — sweeps the old ChromaDB files the previous backend left
behind (often hundreds of MB).

```bash
kyp-mem doctor            # report index size and anything reclaimable
kyp-mem compact --dry-run # show what would be freed, change nothing
kyp-mem compact           # reclaim it
```

### Upgrading from a pre-1.0 install

Earlier versions derived the index location from the vault's *parent* folder,
so two vaults sitting side by side shared one index. Each sync treated the
other vault's notes as deleted, pruned them, and re-embedded its own — meaning
every switch between vaults silently rebuilt the entire index. That churn is
what let the store grow without bound.

Each vault now gets its own index directory. Run this once to clear the old
shared one:

```bash
kyp-mem compact --purge-legacy
```

`kyp-mem doctor` tells you whether you have one.

## Uninstall

```bash
# Remove from Claude Code (keeps your vault data)
kyp-mem uninstall

# Remove from Kimi CLI (also keeps your vault data)
kyp-mem setup-kimi --global --remove
kyp-mem install-kimi-hooks --remove

# Remove from Claude Code and delete ~/.kyp-mem (config, session logs,
# and the default vault). A vault you configured elsewhere is NOT touched.
kyp-mem uninstall --purge

# Remove the npm package
npm uninstall -g kyp-mem
```

## License

MIT
