import sys
import shutil
import hashlib
import chromadb
from pathlib import Path
from contextlib import contextmanager


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

try:
    import fcntl  # POSIX (macOS/Linux)
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def _log(msg: str):
    print(f"[kyp-mem vector] {msg}", file=sys.stderr)


def _clear_chroma_cache():
    """Drop Chroma's process-wide PersistentClient cache so the next
    PersistentClient(path=...) re-reads from disk instead of returning a stale
    cached instance."""
    try:
        from chromadb.api.shared_system_client import SharedSystemClient
        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


class SessionMemory:
    """Semantic session store backed by ChromaDB.

    Several processes touch the same on-disk Chroma directory at once: the web
    UI, the MCP server, and the short-lived Claude Code hooks. ChromaDB's
    PersistentClient is not built for concurrent multi-process writes, so
    interleaved writes can corrupt the HNSW segment's pickle on disk. We guard
    against that two ways:

      1. A cross-process file lock serializes writes (and isolates them from
         reads) so concurrent processes don't clobber each other.
      2. If the index is already corrupt, we detect it and rebuild from the
         markdown vault, which is the source of truth.
    """

    def __init__(self, vault_path: str):
        self.db_path = Path(vault_path).parent / "chroma"
        self.db_path.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.db_path / ".kyp.lock"
        self._open()
        self._heal_if_corrupt()

    # --- connection / recovery -------------------------------------------------

    def _open(self):
        self.client = chromadb.PersistentClient(path=str(self.db_path))
        self.collection = self.client.get_or_create_collection(name="sessions")

    def _rebuild(self):
        """Drop the corrupted index and start fresh.

        Safe because every session is re-embedded from its markdown note on the
        next sync (see Vault._sync_vector_db).

        We first try dropping the collection through Chroma's API (which orphans
        the bad segment and creates a clean one). If that fails, we wipe the
        directory on disk. Either way we must clear Chroma's process-wide client
        cache: PersistentClient instances are cached by path, so without this a
        re-created client would keep pointing at the deleted files (surfacing as
        "attempt to write a readonly database")."""
        _log("index appears corrupt — rebuilding chroma store from the vault")
        try:
            self.client.delete_collection(name="sessions")
            self.collection = self.client.get_or_create_collection(name="sessions")
            return
        except Exception as e:
            _log(f"in-place collection reset failed ({e!r}); wiping store on disk")

        self.client = None
        self.collection = None
        _clear_chroma_cache()
        shutil.rmtree(self.db_path, ignore_errors=True)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self._open()

    def _heal_if_corrupt(self):
        """Force the write/compaction path that surfaces a corrupt segment.

        Corruption only throws when Chroma deserializes the HNSW segment during
        a write. We run a sentinel upsert+delete under the lock; if that raises,
        we rebuild before any real sync runs, so the rebuilt store fills cleanly
        in one pass."""
        sentinel = "__kyp_healthcheck__"
        try:
            with self._locked(write=True):
                # upsert+delete exercises the write/compaction path; the query
                # forces the HNSW segment to load (the read path). Between them
                # they surface both ways a corrupt segment manifests.
                self.collection.upsert(documents=["ok"], ids=[sentinel])
                self.collection.query(query_texts=["ok"], n_results=1)
                self.collection.delete(ids=[sentinel])
        except Exception as e:
            _log(f"health check failed: {e!r}")
            with self._locked(write=True):
                self._rebuild()

    # --- locking ---------------------------------------------------------------

    @contextmanager
    def _locked(self, write: bool):
        if fcntl is None:
            yield
            return
        mode = fcntl.LOCK_EX if write else fcntl.LOCK_SH
        with open(self._lock_path, "a+") as lf:
            fcntl.flock(lf, mode)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    # --- operations ------------------------------------------------------------

    def _write_with_recovery(self, op):
        """Run a write op under the lock. If it fails (e.g. a corrupt segment
        slipped past the init health check), rebuild the store once and retry.

        ``op`` must be self-contained — after a rebuild the collection is empty,
        so an op that derives its work from the current collection state
        naturally repopulates everything on the retry."""
        for attempt in (1, 2):
            try:
                with self._locked(write=True):
                    op()
                return
            except Exception as e:
                if attempt == 1:
                    _log(f"write failed ({e!r}); rebuilding and retrying")
                    with self._locked(write=True):
                        self._rebuild()
                else:
                    _log(f"write failed after rebuild: {e!r}")

    def upsert_session(self, path: str, project: str, content: str):
        meta = {"project": project, "hash": _content_hash(content)}

        def op():
            self.collection.upsert(documents=[content], metadatas=[meta], ids=[path])

        self._write_with_recovery(op)

    def sync_sessions(self, items: dict):
        """Reconcile the store with the current set of session notes.

        ``items`` maps note path -> (project, content). New and changed notes
        (by content hash) are (re)embedded, deleted notes are pruned, and
        unchanged notes are skipped so we don't re-embed the whole corpus on
        every Vault init/refresh."""
        desired = {p: (proj, c, _content_hash(c)) for p, (proj, c) in items.items()}

        def op():
            existing = self.collection.get(include=["metadatas"])
            existing_hash = {
                i: (m or {}).get("hash")
                for i, m in zip(existing["ids"], existing["metadatas"])
            }
            up_ids, up_docs, up_meta = [], [], []
            for p, (proj, c, h) in desired.items():
                if existing_hash.get(p) != h:
                    up_ids.append(p)
                    up_docs.append(c)
                    up_meta.append({"project": proj, "hash": h})
            stale = [i for i in existing_hash if i not in desired]
            if up_ids:
                self.collection.upsert(documents=up_docs, metadatas=up_meta, ids=up_ids)
            if stale:
                self.collection.delete(ids=stale)

        self._write_with_recovery(op)

    def delete_session(self, path: str):
        try:
            with self._locked(write=True):
                self.collection.delete(ids=[path])
        except Exception:
            pass

    def search_sessions(self, query: str, project: str = None, n_results: int = 5):
        where = {"project": project} if project else None
        try:
            with self._locked(write=False):
                return self.collection.query(
                    query_texts=[query],
                    n_results=n_results,
                    where=where,
                )
        except Exception:
            return {"ids": [], "documents": [], "metadatas": [], "distances": []}


session_memory = None


def init_vector_db(vault_path: str):
    global session_memory
    session_memory = SessionMemory(vault_path)


def get_session_memory():
    return session_memory
