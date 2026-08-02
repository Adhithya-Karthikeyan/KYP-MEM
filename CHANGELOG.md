# Changelog

All notable changes to kyp-mem are documented here.

This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] — 2026-08-02

The lightweight release: the Python environment drops from **459 MB to
145 MB** (28 MB for the minimal tier), the MCP server starts ~6x faster, and
a packaging break that made every fresh install since 2026-07-28 crash is
fixed. **Your markdown notes are untouched.** The semantic index changed
format; it rebuilds itself automatically from your notes on first search —
run `kyp-mem compact` afterwards to reclaim the old index's disk (often
hundreds of MB).

### Fixed

- **Fresh installs were broken since 2026-07-28.** The MCP Python SDK
  released v2.0.0 on that date and removed the `FastMCP` import path this
  server is built on; kyp-mem's unbounded `mcp>=1.0.0` pin happily installed
  it and then crashed on import. Now pinned to `mcp>=1.28,<2` (the SDK
  maintainers' supported v1 maintenance line).

### Changed

- **ChromaDB is gone.** The semantic index is now a single SQLite file with
  exact (numpy) nearest-neighbour search. At kyp-mem's scale, exact search
  scores 50,000 chunks in under a millisecond — an approximate index bought
  nothing and cost everything: chromadb's mandatory dependency tree
  (kubernetes, grpcio, onnxruntime, opentelemetry, ~430 MB) was almost the
  entire install, its deleted vectors never returned disk, and dropped
  collections leaked segment directories. The new store reclaims space on
  delete, vacuums on `kyp-mem compact`, and backs up with `cp`.
  Retrieval quality is unchanged: the default embedding model is the exact
  ONNX all-MiniLM-L6-v2 artifact ChromaDB bundled (existing model caches are
  reused), with the same calibrated similarity floors.
- **The MCP server answers ~6x faster after launch** (~1.2 s → ~0.2 s to
  ready). The vault parse + keyword-index build now happens on the first tool
  call instead of at process start. This matters because recent Claude Code
  versions give a stdio server only ~2 seconds before the session's first
  turn proceeds without its tools.
- **Faster installs with uv.** When [uv](https://docs.astral.sh/uv/) is on
  PATH, kyp-mem provisions its Python environment with it (~100x faster venv
  creation, ~60x faster installs, and uv can download a suitable Python by
  itself when the system has none). The plain `python -m venv` + pip flow
  remains the fallback and works exactly as before.

### Added

- **Install tiers.** PyPI users can now choose:
  `pip install kyp-mem` (~28 MB: MCP server, vault, BM25 keyword search,
  hooks), `kyp-mem[vector]` (semantic search, default ONNX model),
  `kyp-mem[vector-lite]` (semantic search with numpy-only static embeddings —
  smallest, measurably weaker floors), `kyp-mem[ui]` (web UI), `kyp-mem[st]`
  (any sentence-transformers model). Without `[vector]`, search degrades to
  keyword-only and says so once on stderr. npm installs keep everything by
  default (`KYP_MEM_LITE=1` opts out).
- **Embedding model choice.** `kyp-mem config embedding_model` now accepts
  `""` (default ONNX MiniLM), `static` (model2vec potion-retrieval-32M),
  `model2vec:<model>`, `st:<model>`, or a bare sentence-transformers name as
  before. Switching models re-embeds automatically on the next search —
  similarity floors travel with the model.
- **MCP tool annotations.** The 12 read-only tools now declare
  `readOnlyHint`, so clients like Claude Code can skip permission prompts
  for them; write/delete tools carry honest destructive/idempotent hints.
  Malformed tool input (bad JSON in `properties`, missing project) returns a
  corrective message the agent can act on instead of a protocol error.
- **`kyp-mem compact` sweeps the old backend.** After upgrading, it removes
  the ChromaDB files the pre-1.2 index directory still holds and reports how
  much disk came back; `kyp-mem doctor` warns when they are present. The
  SQLite index also gets a real integrity check in `doctor`.

## [1.1.0] — 2026-08-01

### Changed

- **Session-start injection is now compact and silent (Claude Code).** The hook
  used to dump the objective plus full summaries of the last 10 sessions (~2k
  tokens) into context and instruct the agent to re-display all of it in its
  first reply. It now injects the objective plus one-line cues per session
  (~63% fewer tokens on a real vault), tells the agent to use them silently,
  and points at `kyp_session_search` for pulling full summaries only when
  actually needed.
- **Kimi CLI hooks display nothing.** Kimi renders hook stdout as a visible
  chat block, so the Kimi hooks only capture (prompts, tool activity, session
  summaries) and memory reaches the agent through the MCP tools instead
  (`kyp_project_context` at session start, `kyp_search` on demand).

### Added

- **Kimi CLI support.** kyp-mem now integrates with Kimi CLI the same way it
  does with Claude Code. `kyp-mem setup-kimi` registers the MCP server in Kimi's
  `mcp.json` (user level with `--global`, project `.kimi-code/mcp.json` by
  default), and `kyp-mem install-kimi-hooks` installs session-capture hooks in
  Kimi's `config.toml` (UserPromptSubmit, PostToolUse, Stop — all silent).
  Both take `--remove` to undo. Postinstall sets Kimi up automatically when it
  detects an existing Kimi installation. Tool activity capture also accepts
  Kimi's `tool_output` payload field alongside Claude's `tool_response`.

## [1.0.0] — 2026-07-26

First stable release. Search was rebuilt and the semantic index changed format,
so this is a major version. **Your markdown notes are untouched** — they are the
source of truth and every index here is derived from them and rebuildable.

### Upgrading

The index is rebuilt automatically on first search. To do it up front, and to
reclaim disk from earlier versions:

```bash
kyp-mem reindex
kyp-mem compact --purge-legacy
```

### Breaking

- **Index location is now unique per vault.** It previously derived from the
  vault's *parent* directory, so two vaults in one folder shared a single index.
  Each sync then saw the other vault's notes as deleted, pruned them, and
  re-embedded its own — meaning every switch between vaults silently rebuilt the
  whole index. This was the root cause of unbounded index growth.
  Run `kyp-mem compact --purge-legacy` once to remove the old shared directory;
  `kyp-mem doctor` reports whether you have one.
- **Index schema v2.** Notes are embedded per markdown section instead of whole,
  so old vectors are not reusable. A reindex happens automatically.
- **`kyp_search` now returns semantic matches too**, not just keyword matches,
  and covers every note rather than only `Sessions/`.

### Added

- **Hybrid retrieval.** BM25 keyword search and semantic search run together and
  are fused with Reciprocal Rank Fusion. Exact identifiers like
  `_prune_stale_logs` and paraphrased questions both work in one call.
- **Section-level chunking.** Results identify the exact heading that matched
  rather than pointing at a whole document.
- **Semantic search over all notes.** `Knowledge.md`, `Architecture.md` and
  `Objective.md` previously had no semantic search at all.
- **Relevance floors.** Search returns nothing when nothing genuinely matches,
  instead of always returning its top N.
- `kyp-mem compact` — reclaims index disk (orphan sweep, full-text merge, vacuum).
  `--dry-run` reports without changing anything; `--purge-legacy` removes the
  pre-1.0 shared index.
- `kyp-mem reindex` — rebuilds the semantic index from your markdown.
- `kyp-mem doctor` now reports index size and anything reclaimable;
  `--deep` exercises the index read/write path.
- Optional stronger embedding model via the `embedding_model` config key
  (requires `sentence-transformers`; falls back to the built-in model if absent).
- Test suite: 169 tests, verified on Python 3.10 and 3.14.

### Fixed

- **Unbounded index growth.** A real vault held 1.2 GB of index for 1.8 MB of
  vectors. Three causes, all addressed: deleted records only tombstoned their
  HNSW slots, dropped collections left their segment directories behind, and
  SQLite's full-text index never merged its segments (62.9 MB of segments for
  184 live embeddings). `kyp-mem compact` now reclaims all three.
- **Session-start latency.** The hook built the entire keyword index just to read
  one objective note and ten session summaries — 906 ms on a 1,281-note vault.
  It now reads only the files it displays. End-to-end: ~1030 ms → ~93 ms.
- **A health check ran on every process start**, doing an upsert/query/delete
  inside `Vault.__init__`. It forced the whole index off disk on every hook fire
  and leaked a tombstoned slot each time. It is now opt-in via `kyp-mem doctor --deep`.
- **`SyntaxError` on Python 3.10.** A nested same-quote f-string in `server.py`
  is only valid on 3.12+, so the MCP server could not be imported on the minimum
  supported version. Ruff is now pinned to `target-version = "py310"` to catch this.
- **Keyword search required every query word to match**, so natural-language
  questions usually returned nothing. Matching is now OR with a coverage bonus.
- **Ranking had no IDF**, so common words outranked the rare ones carrying the
  query's meaning. Replaced with Okapi BM25.
- **Keyword search scanned the entire vocabulary per query word.** Replaced with
  a real inverted index and binary-search prefix expansion.
- **Every write re-parsed the whole vault** and rebuilt the entire index. Index
  updates are now incremental.
- Snippets now centre on the densest match rather than the first one, which
  usually landed on a heading.
- Backlinks resolve correctly when the target note is created after the link.

### Removed

- The `anthropic` dependency, which was declared but never imported. Session
  summarization shells out to the Claude Code CLI and reuses its login.
