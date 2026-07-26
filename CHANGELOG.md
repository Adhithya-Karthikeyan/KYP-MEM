# Changelog

All notable changes to kyp-mem are documented here.

This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
