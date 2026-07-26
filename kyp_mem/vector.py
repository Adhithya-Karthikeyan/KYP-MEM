"""Semantic index over the vault, backed by ChromaDB.

Rewritten to fix four things the previous version got wrong:

  1. **It only indexed ``Sessions/``.** Knowledge.md, Objective.md and every
     other project note had no semantic search at all.
  2. **It embedded whole notes.** A 3 KB session became one vector, which
     averaged its distinct sections into a point that matched everything
     vaguely and nothing precisely. We now embed heading-scoped chunks.
  3. **It connected eagerly and ran a write on every process start.** A
     health check in the constructor did an upsert + query + delete each time
     any process built a Vault — including short-lived hooks — which forced
     the whole HNSW segment off disk and leaked a tombstoned slot per run.
     Connection is now lazy and the health check is opt-in (``kyp-mem doctor``).
  4. **It returned its top 5 no matter how bad they were.** Results now have
     to clear a similarity floor.

Concurrency: the web UI, the MCP server and Claude Code hooks all touch the
same directory, so writes are serialised with a cross-process file lock.
"""

import hashlib
import os
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .chunking import chunk_note

try:
    import fcntl  # POSIX (macOS/Linux)
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

# Bumped when the on-disk chunk layout changes, so an upgrade starts a clean
# collection instead of mixing incompatible records.
SCHEMA_VERSION = "v2"

# Chroma rejects very large single adds; stay well under the limit.
BATCH_SIZE = 500

# Cosine distance is in [0, 2]; similarity = 1 - distance. Below this, a hit is
# noise rather than a weak match.
#
# Default for semantic-only callers. Measured on a real vault with the default
# MiniLM embeddings, off-topic queries peaked at 0.244, so this sits just above
# that. Vault.search overrides it with a two-tier scheme that relaxes the floor
# for notes the keyword index independently found — see vault.py.
DEFAULT_MIN_SIMILARITY = 0.28

# Explicit HNSW parameters. The previous store used the defaults and never
# reclaimed deleted slots, growing to ~236k allocated slots for 1191 records.
HNSW_CONFIG = {
    "space": "cosine",       # correct metric for normalised text embeddings
    "max_neighbors": 16,
    "ef_construction": 100,
    "ef_search": 64,
}


def _log(msg: str):
    print(f"[kyp-mem vector] {msg}", file=sys.stderr)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# The index used to live at ``Path(vault).parent / "chroma"``. That is not
# unique to a vault: two vaults side by side (say ~/docs/memory and
# ~/docs/project-docs) resolved to the *same* directory. Each sync then saw the
# other vault's notes as deleted, pruned them, and re-embedded its own — so
# every alternation between vaults was a full wipe and rebuild of the index.
# That churn, not merely Chroma's lack of compaction, is what grows the store
# without bound.
LEGACY_INDEX_DIRNAME = "chroma"


def index_dir_for(vault_path) -> Path:
    """Index location for a vault — unique per vault, so two never collide."""
    root = Path(vault_path).expanduser().resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    return root.parent / f".kyp-index-{root.name}-{digest}"


def legacy_index_dir_for(vault_path) -> Path:
    return Path(vault_path).expanduser().resolve().parent / LEGACY_INDEX_DIRNAME


def _profile_name(embedding_model: str) -> str:
    """Collection-name-safe slug identifying which model produced the vectors."""
    if not embedding_model:
        return "default"
    slug = "".join(c if c.isalnum() else "-" for c in embedding_model).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:40] or "custom"


@dataclass
class VectorHit:
    chunk_id: str
    doc_path: str
    project: str
    title: str
    heading: str
    text: str
    similarity: float


def _clear_chroma_cache():
    """Drop Chroma's process-wide PersistentClient cache.

    Clients are cached by path, so after deleting files on disk a re-created
    client would otherwise keep pointing at them ("attempt to write a readonly
    database").
    """
    try:
        from chromadb.api.shared_system_client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


class VectorStore:
    """Chunk-level semantic index over every note in the vault.

    Construction is cheap and does no I/O beyond creating the directory — the
    Chroma client is only built on first actual use, so a session-start hook
    that never searches never pays for the index.
    """

    def __init__(self, vault_path: str, embedding_profile: str | None = None,
                 embedding_model: str | None = None):
        self.vault_path = str(Path(vault_path).expanduser().resolve())
        self.db_path = index_dir_for(vault_path)
        self.legacy_db_path = legacy_index_dir_for(vault_path)

        if embedding_model is None:
            try:
                from .config import get_embedding_model

                embedding_model = get_embedding_model()
            except Exception:
                embedding_model = ""
        self.embedding_model = embedding_model or ""
        self.embedding_profile = embedding_profile or _profile_name(self.embedding_model)

        # The profile is part of the collection name: vectors from different
        # models are not comparable, so switching models starts a separate
        # collection rather than silently mixing incompatible embeddings.
        self.collection_name = f"notes-{SCHEMA_VERSION}-{self.embedding_profile}"
        self._lock_path = self.db_path / ".kyp.lock"
        self._client = None
        self._collection = None
        # Set whenever the collection is dropped. A rebuild triggered by a
        # single-note write would otherwise leave the index holding only that
        # note, with the rest of the vault silently missing from search until
        # something happened to trigger a full sync.
        self.needs_full_sync = False

    # --- lazy connection ------------------------------------------------------

    @property
    def collection(self):
        if self._collection is None:
            self._connect()
        return self._collection

    def _embedding_function(self):
        """The configured embedding model, or None for Chroma's bundled default.

        A missing optional dependency must never break search, so a configured
        model that cannot be loaded falls back to the default with a warning
        rather than raising.
        """
        if not self.embedding_model:
            return None
        try:
            from chromadb.utils import embedding_functions

            return embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self.embedding_model
            )
        except Exception as e:
            _log(
                f"embedding model {self.embedding_model!r} unavailable ({e!r}); "
                "falling back to the built-in model. "
                "Install it with: pip install sentence-transformers"
            )
            return None

    def _connect(self):
        import chromadb

        self.db_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.db_path))
        kwargs = {"name": self.collection_name}
        ef = self._embedding_function()
        if ef is not None:
            kwargs["embedding_function"] = ef
        try:
            self._collection = self._client.get_or_create_collection(
                configuration={"hnsw": dict(HNSW_CONFIG)}, **kwargs
            )
        except Exception:
            # Older Chroma builds only accept the metadata form.
            self._collection = self._client.get_or_create_collection(
                metadata={
                    "hnsw:space": HNSW_CONFIG["space"],
                    "hnsw:M": HNSW_CONFIG["max_neighbors"],
                    "hnsw:construction_ef": HNSW_CONFIG["ef_construction"],
                    "hnsw:search_ef": HNSW_CONFIG["ef_search"],
                },
                **kwargs,
            )

    def close(self):
        self._collection = None
        self._client = None

    def is_connected(self) -> bool:
        return self._collection is not None

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

    # --- recovery -------------------------------------------------------------

    def rebuild(self):
        """Drop the collection and start clean.

        Safe because the markdown vault is the source of truth and the next
        sync re-embeds everything.
        """
        _log("rebuilding semantic index")
        self.needs_full_sync = True
        try:
            if self._client is None:
                self._connect()
            self._client.delete_collection(name=self.collection_name)
            self._collection = None
            self._connect()
            return
        except Exception as e:
            _log(f"in-place reset failed ({e!r}); wiping store on disk")

        self.close()
        _clear_chroma_cache()
        shutil.rmtree(self.db_path, ignore_errors=True)
        self._connect()

    def health_check(self) -> tuple:
        """Exercise the write and read paths to surface a corrupt segment.

        Deliberately *not* called during construction — this is what the old
        code did on every process start, and it was the single largest source
        of both latency and index bloat. ``kyp-mem doctor`` calls it instead.
        """
        sentinel = "__kyp_healthcheck__"
        try:
            with self._locked(write=True):
                self.collection.upsert(documents=["ok"], ids=[sentinel])
                self.collection.query(query_texts=["ok"], n_results=1)
                self.collection.delete(ids=[sentinel])
            return True, "ok"
        except Exception as e:
            return False, repr(e)

    def _write(self, op, description: str = "write"):
        """Run a write under the lock, rebuilding once if it fails.

        Unlike the previous version this reports failure to the caller instead
        of swallowing it — a silently failed prune is how stale records
        accumulated unnoticed.
        """
        # Plain retry first. Dropping the whole collection on the first failure
        # treats every transient error — a locking conflict, a full disk, an
        # interrupted write — as index corruption, which is both drastic and
        # usually wrong. Rebuilding is reserved for the case where a second
        # straightforward attempt also fails.
        try:
            with self._locked(write=True):
                op()
            return True
        except Exception as first:
            _log(f"{description} failed ({first!r}); retrying")

        try:
            with self._locked(write=True):
                op()
            return True
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

    def _chunk_records(self, path, project, title, content, tags, note_hash):
        ids, docs, metas = [], [], []
        for chunk in chunk_note(path, content):
            ids.append(chunk.chunk_id)
            docs.append(chunk.embed_text(title))
            metas.append(
                {
                    "doc_path": path,
                    "project": project or "",
                    "title": title or "",
                    "heading": chunk.heading,
                    "ordinal": chunk.ordinal,
                    "hash": note_hash,
                    "tags": ",".join(str(t) for t in (tags or [])),
                }
            )
        return ids, docs, metas

    def _existing_doc_state(self):
        """Map ``doc_path -> (hash, {chunk ids})`` for everything indexed."""
        got = self.collection.get(include=["metadatas"])
        state = {}
        for cid, meta in zip(got.get("ids", []), got.get("metadatas", []) or []):
            meta = meta or {}
            doc = meta.get("doc_path")
            if not doc:
                continue
            entry = state.setdefault(doc, [meta.get("hash"), set()])
            entry[1].add(cid)
        return {k: (v[0], v[1]) for k, v in state.items()}

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
            existing = self._existing_doc_state()

            stale_ids = []
            add_ids, add_docs, add_metas = [], [], []

            for path, (project, title, content, tags, h) in desired.items():
                prev_hash, prev_ids = existing.get(path, (None, set()))
                if prev_hash == h:
                    summary["unchanged"] += 1
                    continue
                if prev_ids:
                    # Chunk count can shrink, so drop the old ids explicitly
                    # rather than relying on upsert to overwrite them.
                    stale_ids.extend(prev_ids)
                    summary["updated"] += 1
                else:
                    summary["added"] += 1
                ids, cdocs, metas = self._chunk_records(path, project, title, content, tags, h)
                add_ids.extend(ids)
                add_docs.extend(cdocs)
                add_metas.extend(metas)

            for path, (_, prev_ids) in existing.items():
                if path not in desired:
                    stale_ids.extend(prev_ids)
                    summary["removed"] += 1

            if stale_ids:
                for i in range(0, len(stale_ids), BATCH_SIZE):
                    self.collection.delete(ids=stale_ids[i : i + BATCH_SIZE])
            for i in range(0, len(add_ids), BATCH_SIZE):
                self.collection.upsert(
                    ids=add_ids[i : i + BATCH_SIZE],
                    documents=add_docs[i : i + BATCH_SIZE],
                    metadatas=add_metas[i : i + BATCH_SIZE],
                )

        summary["ok"] = self._write(op, "sync")
        if summary["ok"]:
            self.needs_full_sync = False
        return summary

    def upsert_note(self, path: str, project: str, title: str, content: str, tags=None):
        """Re-embed a single note, replacing whatever was indexed for it."""
        h = content_hash(content)
        ids, docs, metas = self._chunk_records(path, project, title, content, tags, h)

        def op():
            self.collection.delete(where={"doc_path": path})
            for i in range(0, len(ids), BATCH_SIZE):
                self.collection.upsert(
                    ids=ids[i : i + BATCH_SIZE],
                    documents=docs[i : i + BATCH_SIZE],
                    metadatas=metas[i : i + BATCH_SIZE],
                )

        return self._write(op, f"upsert {path}")

    def delete_note(self, path: str):
        def op():
            self.collection.delete(where={"doc_path": path})

        return self._write(op, f"delete {path}")

    # --- querying -------------------------------------------------------------

    def search(
        self,
        query: str,
        project: str | None = None,
        n_results: int = 8,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        sessions_only: bool = False,
    ) -> list:
        """Semantic search over chunks, best first.

        Returns only hits clearing ``min_similarity`` — an empty list is a
        truthful "nothing relevant", which the previous version could not say.
        """
        if not query or not query.strip():
            return []

        clauses = []
        if project:
            clauses.append({"project": project})
        where = None
        if clauses:
            where = clauses[0] if len(clauses) == 1 else {"$and": clauses}

        try:
            with self._locked(write=False):
                res = self.collection.query(
                    query_texts=[query],
                    n_results=max(n_results * 3, n_results),
                    where=where,
                )
        except Exception as e:
            _log(f"search failed: {e!r}")
            return []

        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        hits = []
        for cid, doc, meta, dist in zip(ids, docs, metas, dists):
            meta = meta or {}
            similarity = 1.0 - float(dist)
            if similarity < min_similarity:
                continue
            doc_path = meta.get("doc_path", cid.split("#")[0])
            if sessions_only and "/Sessions/" not in doc_path and not doc_path.startswith("Sessions/"):
                continue
            hits.append(
                VectorHit(
                    chunk_id=cid,
                    doc_path=doc_path,
                    project=meta.get("project", ""),
                    title=meta.get("title", ""),
                    heading=meta.get("heading", ""),
                    text=doc or "",
                    similarity=similarity,
                )
            )
        return hits[:n_results]

    def stats(self) -> dict:
        try:
            with self._locked(write=False):
                count = self.collection.count()
            docs = len(self._existing_doc_state())
            return {"chunks": count, "documents": docs, "collection": self.collection_name}
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

    Set ``KYP_NO_VECTOR=1`` to skip it entirely — useful on the hook hot path
    and for environments where the embedding runtime is unavailable.
    """
    return os.environ.get("KYP_NO_VECTOR", "").strip() not in ("1", "true", "yes")
