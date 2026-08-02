"""Semantic index over the vault: one SQLite file + numpy brute-force search.

This replaces the ChromaDB backend. Chroma pulled in ~430 MB of transitive
dependencies (kubernetes, grpcio, onnxruntime, opentelemetry) for what is, at
kyp-mem's scale, a small exact-nearest-neighbour problem: even 50,000 chunks
of 384-dim float32 score in under a millisecond with a plain dot product. An
ANN graph buys nothing here, and Chroma's HNSW store famously never returned
disk (deleted slots tombstone forever; dropped collections leave their segment
directories behind).

Design:

  - Storage is a single ``semantic.sqlite3`` in the per-vault index directory:
    a ``chunks`` table (metadata + embedding blob) and a ``meta`` table
    (schema version, embedder spec, dimension, generation counter). DELETE
    actually reclaims space, VACUUM compacts, backup is ``cp``.
  - Search loads the chunk matrix into numpy once and caches it per process,
    invalidated by the ``generation`` counter which every write bumps — so
    concurrent processes see each other's writes without re-reading the table
    on every query.
  - Embeddings are computed *here* (see embedder.py), not inside the store's
    dependency. Vectors from different models are not comparable, so the
    embedder spec is persisted in ``meta``; a mismatch on connect drops the
    stale vectors and flags ``needs_full_sync`` — the markdown vault is the
    source of truth and re-embedding is cheap.

Concurrency: the web UI, the MCP server and Claude Code hooks all touch the
same file. SQLite runs in WAL mode with a busy timeout, and multi-step
read-modify-write operations (sync's diff-then-apply) are additionally
serialised with the same cross-process file lock the Chroma backend used.

Construction stays cheap — no file I/O, no model load — so a session-start
hook that never searches never pays for any of this.
"""

import hashlib
import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .chunking import chunk_note
from .embedder import EmbedderUnavailable, floors_for_spec, load_embedder, resolve_spec

try:
    import fcntl  # POSIX (macOS/Linux)
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

# Bumped when the on-disk layout changes, so an upgrade starts a clean index
# instead of mixing incompatible records. v3 = the SQLite backend.
SCHEMA_VERSION = "v3"

DB_FILENAME = "semantic.sqlite3"

# Encode in bounded batches so a full-vault re-embed doesn't hold every
# tokenised text in memory at once (matters for the ONNX/ST tiers).
ENCODE_BATCH = 128


def _log(msg: str):
    print(f"[kyp-mem vector] {msg}", file=sys.stderr)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# The index used to live at ``Path(vault).parent / "chroma"``. That is not
# unique to a vault: two vaults side by side resolved to the *same* directory
# and repeatedly wiped each other's index. The digest keeps directories unique
# per vault path.
LEGACY_INDEX_DIRNAME = "chroma"


def index_dir_for(vault_path) -> Path:
    """Index location for a vault — unique per vault, so two never collide."""
    root = Path(vault_path).expanduser().resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    return root.parent / f".kyp-index-{root.name}-{digest}"


def legacy_index_dir_for(vault_path) -> Path:
    return Path(vault_path).expanduser().resolve().parent / LEGACY_INDEX_DIRNAME


@dataclass
class VectorHit:
    chunk_id: str
    doc_path: str
    project: str
    title: str
    heading: str
    text: str
    similarity: float


class VectorStore:
    """Chunk-level semantic index over every note in the vault.

    Construction does no I/O. The SQLite file is opened, and the embedding
    model loaded, on first actual use.
    """

    def __init__(self, vault_path: str, embedding_profile: str | None = None,
                 embedding_model: str | None = None):
        self.vault_path = str(Path(vault_path).expanduser().resolve())
        self.db_path = index_dir_for(vault_path)
        self.legacy_db_path = legacy_index_dir_for(vault_path)
        self.db_file = self.db_path / DB_FILENAME

        if embedding_model is None:
            try:
                from .config import get_embedding_model

                embedding_model = get_embedding_model()
            except Exception:
                embedding_model = ""
        # The canonical spec doubles as the compatibility key persisted in the
        # meta table; ``embedding_profile`` is kept as an explicit override for
        # tests and callers that had one.
        self.embedding_model = embedding_model or ""
        self.embedding_profile = embedding_profile or resolve_spec(self.embedding_model)

        self._lock_path = self.db_path / ".kyp.lock"
        self._connected = False
        self._embedder = None
        # In-process cache of the chunk matrix, invalidated by generation.
        self._cache_generation = -1
        self._cache_rows = None
        self._cache_matrix = None
        # Set whenever the stored vectors are dropped (rebuild, model switch).
        # A single-note write after that would otherwise leave the index
        # holding only that note until something triggered a full sync.
        self.needs_full_sync = False

    # --- similarity floors ----------------------------------------------------

    @property
    def floor_alone(self) -> float:
        """Strict floor for hits with no independent keyword evidence."""
        return floors_for_spec(self.embedding_profile)[0]

    @property
    def floor_corroborated(self) -> float:
        """Lenient floor for hits the keyword index also found."""
        return floors_for_spec(self.embedding_profile)[1]

    # --- lazy connection ------------------------------------------------------

    @contextmanager
    def _db(self):
        """A fresh connection per operation: safe under FastMCP's thread pool,
        ~µs to open. The body runs inside a transaction (committed on success,
        rolled back on exception) and the connection is always closed —
        ``sqlite3``'s own ``with con`` only manages the transaction, which is
        how connection leaks happen.
        """
        self.db_path.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db_file), timeout=10)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA synchronous=NORMAL")
            with con:
                yield con
        finally:
            con.close()

    def _connect(self):
        """Ensure the schema exists and the stored vectors match our model."""
        with self._db() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS meta (
                       key TEXT PRIMARY KEY, value TEXT NOT NULL)"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                       chunk_id TEXT PRIMARY KEY,
                       doc_path TEXT NOT NULL,
                       project  TEXT NOT NULL DEFAULT '',
                       title    TEXT NOT NULL DEFAULT '',
                       heading  TEXT NOT NULL DEFAULT '',
                       ordinal  INTEGER NOT NULL DEFAULT 0,
                       hash     TEXT NOT NULL DEFAULT '',
                       tags     TEXT NOT NULL DEFAULT '',
                       text     TEXT NOT NULL DEFAULT '',
                       embedding BLOB NOT NULL)"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_path)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project)")

            stored = dict(con.execute("SELECT key, value FROM meta"))
            want = {"schema": SCHEMA_VERSION, "embedder": self.embedding_profile}
            if stored.get("schema") != want["schema"] or stored.get("embedder") != want["embedder"]:
                had_rows = con.execute("SELECT EXISTS(SELECT 1 FROM chunks)").fetchone()[0]
                if had_rows:
                    _log(
                        f"index was built with {stored.get('embedder') or 'an older layout'}; "
                        f"now configured for {want['embedder']} — re-embedding from notes"
                    )
                    con.execute("DELETE FROM chunks")
                    self.needs_full_sync = True
                con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema', ?), ('embedder', ?)",
                    (want["schema"], want["embedder"]),
                )
                con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('generation', "
                    "COALESCE((SELECT CAST(value AS INTEGER) + 1 FROM meta WHERE key='generation'), 0))"
                )
        self._connected = True

    def _ensure_connected(self):
        if not self._connected:
            self._connect()

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = load_embedder(self.embedding_profile)
        return self._embedder

    def close(self):
        # Per-operation connections mean there is nothing to close; this
        # resets the lazy state so the store is inert again (vault.write only
        # updates the index when it is already live).
        self._connected = False
        self._embedder = None
        self._cache_generation = -1
        self._cache_rows = None
        self._cache_matrix = None

    def is_connected(self) -> bool:
        return self._connected

    # --- locking --------------------------------------------------------------

    @contextmanager
    def _locked(self, write: bool):
        if fcntl is None:
            yield
            return
        self.db_path.mkdir(parents=True, exist_ok=True)
        mode = fcntl.LOCK_EX if write else fcntl.LOCK_SH
        with open(self._lock_path, "a+") as lf:
            fcntl.flock(lf, mode)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    # --- encoding -------------------------------------------------------------

    def _encode(self, texts: list):
        import numpy as np

        embedder = self._get_embedder()
        parts = [
            embedder.encode(texts[i : i + ENCODE_BATCH])
            for i in range(0, len(texts), ENCODE_BATCH)
        ]
        return np.vstack(parts) if parts else np.zeros((0, embedder.dim), dtype=np.float32)

    def _chunk_records(self, path, project, title, content, tags, note_hash):
        """Rows for one note, minus embeddings (encoded in bulk by the caller)."""
        rows = []
        for chunk in chunk_note(path, content):
            rows.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "doc_path": path,
                    "project": project or "",
                    "title": title or "",
                    "heading": chunk.heading,
                    "ordinal": chunk.ordinal,
                    "hash": note_hash,
                    "tags": ",".join(str(t) for t in (tags or [])),
                    "text": chunk.embed_text(title),
                }
            )
        return rows

    def _insert_rows(self, con, rows, vectors):
        con.executemany(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, doc_path, project, title, heading, ordinal, hash, tags, text, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r["chunk_id"], r["doc_path"], r["project"], r["title"], r["heading"],
                    r["ordinal"], r["hash"], r["tags"], r["text"],
                    vectors[i].tobytes(),
                )
                for i, r in enumerate(rows)
            ],
        )

    @staticmethod
    def _bump_generation(con):
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('generation', "
            "COALESCE((SELECT CAST(value AS INTEGER) + 1 FROM meta WHERE key='generation'), 0))"
        )

    # --- recovery -------------------------------------------------------------

    def rebuild(self):
        """Drop every stored vector and start clean.

        Safe because the markdown vault is the source of truth and the next
        sync re-embeds everything.
        """
        _log("rebuilding semantic index")
        self.needs_full_sync = True
        try:
            self._ensure_connected()
            with self._db() as con:
                con.execute("DELETE FROM chunks")
                self._bump_generation(con)
            return
        except Exception as e:
            _log(f"in-place reset failed ({e!r}); recreating the database file")

        self._connected = False
        try:
            self.db_file.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(self.db_file) + suffix).unlink(missing_ok=True)
        except OSError as e:
            _log(f"could not remove database file: {e!r}")
        self._connect()

    def health_check(self) -> tuple:
        """Exercise the write, embed and read paths end to end.

        Deliberately not called during construction — ``kyp-mem doctor`` calls
        it instead.
        """
        sentinel = "__kyp_healthcheck__"
        try:
            self._ensure_connected()
            vec = self._encode(["ok"])
            with self._locked(write=True), self._db() as con:
                con.execute(
                    "INSERT OR REPLACE INTO chunks (chunk_id, doc_path, embedding) VALUES (?, ?, ?)",
                    (sentinel, sentinel, vec[0].tobytes()),
                )
                con.execute("DELETE FROM chunks WHERE chunk_id = ?", (sentinel,))
            self.search("ok", n_results=1, min_similarity=2.0)
            return True, "ok"
        except Exception as e:
            return False, repr(e)

    def _write(self, op, description: str = "write"):
        """Run a write under the lock, rebuilding once if it fails twice.

        A transient error (lock conflict, full disk) gets a plain retry first;
        dropping the index is reserved for persistent failure, and failure is
        reported to the caller rather than swallowed.

        A missing embedding runtime is neither transient nor index corruption
        — rebuilding would destroy good vectors over an uninstalled package —
        so it fails fast with the install hint instead.
        """
        try:
            with self._locked(write=True):
                op()
            return True
        except EmbedderUnavailable as e:
            _log(str(e))
            return False
        except Exception as first:
            _log(f"{description} failed ({first!r}); retrying")

        try:
            with self._locked(write=True):
                op()
            return True
        except EmbedderUnavailable as e:
            _log(str(e))
            return False
        except Exception as second:
            _log(f"{description} failed again ({second!r}); rebuilding the index")

        try:
            with self._locked(write=True):
                self.rebuild()
        except Exception as rebuild_err:
            _log(f"rebuild failed: {rebuild_err!r}")
            return False

        try:
            with self._locked(write=True):
                op()
            return True
        except Exception as final:
            _log(f"{description} failed after rebuild: {final!r}")
            return False

    # --- indexing -------------------------------------------------------------

    def _existing_doc_state(self, con) -> dict:
        """Map ``doc_path -> hash`` for everything indexed."""
        return dict(con.execute("SELECT DISTINCT doc_path, hash FROM chunks"))

    def sync(self, docs: dict) -> dict:
        """Reconcile the index with the vault.

        ``docs`` maps note path -> ``(project, title, content, tags)``. Only
        notes whose content hash changed are re-embedded; removed notes are
        pruned. Returns a summary so callers can report what happened.
        """
        summary = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "ok": True}

        desired = {
            path: (project, title, content, tags, content_hash(content))
            for path, (project, title, content, tags) in docs.items()
        }

        def op():
            # Reset the counters: _write may run this op twice (retry), and
            # counts accumulated by a half-failed first attempt would double.
            summary.update(added=0, updated=0, removed=0, unchanged=0)
            self._ensure_connected()
            with self._db() as con:
                existing = self._existing_doc_state(con)

            stale_paths = []
            rows = []
            for path, (project, title, content, tags, h) in desired.items():
                prev_hash = existing.get(path)
                if prev_hash == h:
                    summary["unchanged"] += 1
                    continue
                if prev_hash is not None:
                    # Chunk count can shrink, so drop the old rows explicitly
                    # rather than relying on REPLACE to overwrite them.
                    stale_paths.append(path)
                    summary["updated"] += 1
                else:
                    summary["added"] += 1
                rows.extend(self._chunk_records(path, project, title, content, tags, h))

            for path in existing:
                if path not in desired:
                    stale_paths.append(path)
                    summary["removed"] += 1

            # Encode outside the transaction: embedding a large sync can take
            # a while and must not hold the database write lock.
            vectors = self._encode([r["text"] for r in rows])

            with self._db() as con:
                for path in stale_paths:
                    con.execute("DELETE FROM chunks WHERE doc_path = ?", (path,))
                self._insert_rows(con, rows, vectors)
                self._bump_generation(con)

        ok = self._write(op, "sync")
        if not ok:
            # The counts describe work that was rolled back, not work done.
            return {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "ok": False}
        summary["ok"] = True
        self.needs_full_sync = False
        return summary

    def upsert_note(self, path: str, project: str, title: str, content: str, tags=None):
        """Re-embed a single note, replacing whatever was indexed for it."""
        h = content_hash(content)
        rows = self._chunk_records(path, project, title, content, tags, h)

        def op():
            self._ensure_connected()
            vectors = self._encode([r["text"] for r in rows])
            with self._db() as con:
                con.execute("DELETE FROM chunks WHERE doc_path = ?", (path,))
                self._insert_rows(con, rows, vectors)
                self._bump_generation(con)

        return self._write(op, f"upsert {path}")

    def delete_note(self, path: str):
        def op():
            self._ensure_connected()
            with self._db() as con:
                con.execute("DELETE FROM chunks WHERE doc_path = ?", (path,))
                self._bump_generation(con)

        return self._write(op, f"delete {path}")

    # --- querying -------------------------------------------------------------

    def _load_cache(self):
        """(rows, matrix) for the whole index, reloaded when generation moves.

        Loading everything and filtering with boolean masks is simpler and, at
        this scale, faster than per-query SQL: the matrix is a few MB and the
        reload only happens after a write.
        """
        import numpy as np

        with self._db() as con:
            gen_row = con.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
            generation = int(gen_row[0]) if gen_row else 0
            if generation == self._cache_generation and self._cache_rows is not None:
                return self._cache_rows, self._cache_matrix
            rows = con.execute(
                "SELECT chunk_id, doc_path, project, title, heading, text, embedding FROM chunks"
            ).fetchall()

        metas = [r[:6] for r in rows]
        if rows:
            dim = len(rows[0][6]) // 4
            matrix = np.frombuffer(b"".join(r[6] for r in rows), dtype=np.float32)
            matrix = matrix.reshape(len(rows), dim)
        else:
            matrix = np.zeros((0, 0), dtype=np.float32)

        self._cache_generation = generation
        self._cache_rows = metas
        self._cache_matrix = matrix
        return metas, matrix

    def search(
        self,
        query: str,
        project: str | None = None,
        n_results: int = 8,
        min_similarity: float | None = None,
        sessions_only: bool = False,
    ) -> list:
        """Semantic search over chunks, best first.

        Returns only hits clearing the similarity floor — an empty list is a
        truthful "nothing relevant". The default floor comes from the
        configured embedding model, because score distributions differ between
        models (see embedder.py).
        """
        if not query or not query.strip():
            return []
        if min_similarity is None:
            min_similarity = self.floor_alone

        try:
            self._ensure_connected()
            query_vec = self._encode([query])[0]
            with self._locked(write=False):
                metas, matrix = self._load_cache()
        except EmbedderUnavailable as e:
            _log(str(e))
            return []
        except Exception as e:
            _log(f"search failed: {e!r}")
            return []

        if not metas or matrix.shape[0] == 0 or matrix.shape[1] != query_vec.shape[0]:
            if metas and matrix.shape[1] != query_vec.shape[0]:
                _log(
                    f"stored vectors are {matrix.shape[1]}-dim but the model produces "
                    f"{query_vec.shape[0]}-dim — reindex with: kyp-mem reindex"
                )
            return []

        sims = matrix @ query_vec

        hits = []
        order = sims.argsort()[::-1]
        for idx in order:
            similarity = float(sims[idx])
            if similarity < min_similarity:
                break  # sorted descending; nothing further can clear the floor
            chunk_id, doc_path, chunk_project, title, heading, text = metas[idx]
            if project and chunk_project != project:
                continue
            if sessions_only and "/Sessions/" not in doc_path and not doc_path.startswith("Sessions/"):
                continue
            hits.append(
                VectorHit(
                    chunk_id=chunk_id,
                    doc_path=doc_path,
                    project=chunk_project,
                    title=title,
                    heading=heading,
                    text=text,
                    similarity=similarity,
                )
            )
            if len(hits) >= n_results:
                break
        return hits

    def stats(self) -> dict:
        try:
            self._ensure_connected()
            with self._locked(write=False), self._db() as con:
                chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                docs = con.execute("SELECT COUNT(DISTINCT doc_path) FROM chunks").fetchone()[0]
            return {"chunks": chunks, "documents": docs, "embedder": self.embedding_profile}
        except Exception as e:
            return {"chunks": 0, "documents": 0, "error": repr(e)}


# --- module-level accessor ----------------------------------------------------

_store = None


def init_vector_db(vault_path: str, embedding_profile: str | None = None):
    """Register the store. Cheap — no connection is opened here."""
    global _store
    _store = VectorStore(vault_path, embedding_profile=embedding_profile)
    return _store


def get_session_memory():
    return _store


def reset_vector_db():
    """Drop the module-level store (used by tests and by vault-path changes)."""
    global _store
    if _store is not None:
        _store.close()
    _store = None


def vector_enabled() -> bool:
    """Whether semantic indexing should run at all.

    ``KYP_NO_VECTOR=1`` disables it explicitly. A missing numpy (the base
    install without the [vector] extra) disables it implicitly — BM25 keyword
    search still covers every note, so this is a degradation, not an outage.
    """
    if os.environ.get("KYP_NO_VECTOR", "").strip() in ("1", "true", "yes"):
        return False
    try:
        import importlib.util

        if importlib.util.find_spec("numpy") is None:
            _log("numpy not installed — semantic search off. pip install 'kyp-mem[vector]'")
            return False
    except Exception:
        return False
    return True
