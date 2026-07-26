"""Vault — markdown file storage with frontmatter, wikilinks, and indexing."""

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .search import hybrid_search
from .textindex import BM25Index
from .vector import init_vector_db, vector_enabled

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Minimum share of a query's information content a keyword hit must match to
# enter the ranking. RRF scores by rank, so without an absolute gate the single
# weak match for an off-topic query becomes rank 1 and looks like an answer.
#
# Measured coverage: off-topic queries that clip one incidental word land at
# 0.15-0.28 ("thai green curry recipe" hitting a note named GreenLeaf; "tax
# return deadline 2026" hitting a timestamped session filename), while relevant
# queries that genuinely need keyword evidence sit at 0.34-1.00. 0.30 is the
# gap. The margin is real but not wide, and it comes from a small sample —
# re-measure before moving it. Queries whose keyword coverage falls below this
# are not lost: strong semantic similarity still carries them on its own.
MIN_KEYWORD_IDF_COVERAGE = 0.30

# Two similarity floors, because one cannot work.
#
# Measured on a real vault with the default MiniLM embeddings, the relevant and
# irrelevant score ranges *overlap*: an off-topic query peaked at 0.244 while a
# genuinely relevant short note scored 0.190. Any single threshold therefore
# either admits junk or drops real answers.
#
# So the floor depends on how much evidence there is. Semantic similarity on
# its own must clear the strict bar. A note the keyword index independently
# found for the same query only has to clear the lenient one — two weak,
# independent signals agreeing is stronger evidence than either alone.
#
# A better embedding model narrows the overlap and is the real fix; see
# `embedding_profile` in vector.py.
SEMANTIC_FLOOR_ALONE = 0.28
SEMANTIC_FLOOR_CORROBORATED = 0.15


def is_session_path(path: str) -> bool:
    return "/Sessions/" in path or path.startswith("Sessions/")


@dataclass
class Note:
    path: str
    title: str
    content: str
    properties: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    links: list = field(default_factory=list)
    created: str = ""
    updated: str = ""

    @property
    def folder(self) -> str:
        parts = self.path.split("/")
        return parts[0] if len(parts) > 1 else ""


def parse_note(path: str, raw: str) -> Note:
    content = raw
    properties = {}

    fm_match = FRONTMATTER_RE.match(raw)
    if fm_match:
        try:
            properties = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError:
            properties = {}
        content = raw[fm_match.end():]

    tags = properties.pop("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    links = WIKILINK_RE.findall(content)
    links = [l.split("#")[0].strip() for l in links]
    links = list(set(links))

    title = Path(path).stem
    for line in content.strip().split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break

    created = properties.pop("created", "")
    updated = properties.pop("updated", "")
    if isinstance(created, datetime):
        created = created.strftime("%Y-%m-%d")
    if isinstance(updated, datetime):
        updated = updated.strftime("%Y-%m-%d")

    return Note(
        path=path,
        title=title,
        content=content,
        properties=properties,
        tags=tags,
        links=links,
        created=str(created) if created else "",
        updated=str(updated) if updated else "",
    )


def serialize_note(note: Note) -> str:
    fm = {}
    if note.tags:
        fm["tags"] = note.tags
    if note.created:
        fm["created"] = note.created
    if note.updated:
        fm["updated"] = note.updated
    fm.update(note.properties)

    parts = []
    if fm:
        parts.append("---")
        parts.append(yaml.dump(fm, default_flow_style=None).strip())
        parts.append("---")
        parts.append("")
    parts.append(note.content)
    return "\n".join(parts)


class Index:
    """In-memory index over the vault.

    Every structure here supports incremental update. Previously any single
    write triggered a full re-parse of every note plus a rebuild of the whole
    word index, so saving one note was O(vault).
    """

    def __init__(self):
        self.notes: dict = {}
        self.tag_index: dict = defaultdict(set)
        self.folder_index: dict = defaultdict(set)
        self.bm25 = BM25Index()
        # Wikilinks are recorded by the *name* written in the source, resolved
        # to a path only at read time. That way a note created later
        # automatically picks up links that already pointed at its name,
        # without needing a global rebuild.
        self._link_sources: dict = defaultdict(set)
        self._name_to_path: dict = {}

    # --- names ----------------------------------------------------------------

    @staticmethod
    def _names_for(note: Note) -> set:
        return {Path(note.path).stem.lower(), (note.title or "").lower()} - {""}

    # --- mutation -------------------------------------------------------------

    def add_note(self, note: Note):
        """Insert or replace a single note."""
        if note.path in self.notes:
            self.remove_note(note.path)

        self.notes[note.path] = note
        for name in self._names_for(note):
            self._name_to_path[name] = note.path
        for tag in note.tags:
            self.tag_index[tag.lower()].add(note.path)
        if note.folder:
            self.folder_index[note.folder].add(note.path)
        for link in note.links:
            self._link_sources[link.lower()].add(note.path)

        self.bm25.add(
            note.path,
            title=note.title,
            body=note.content,
            tags=note.tags,
            path=note.path,
        )

    def remove_note(self, path: str):
        note = self.notes.pop(path, None)
        if note is None:
            return
        for name in self._names_for(note):
            if self._name_to_path.get(name) == path:
                del self._name_to_path[name]
        for tag in note.tags:
            self.tag_index[tag.lower()].discard(path)
            if not self.tag_index[tag.lower()]:
                del self.tag_index[tag.lower()]
        if note.folder:
            self.folder_index[note.folder].discard(path)
            if not self.folder_index[note.folder]:
                del self.folder_index[note.folder]
        for link in note.links:
            self._link_sources[link.lower()].discard(path)
            if not self._link_sources[link.lower()]:
                del self._link_sources[link.lower()]
        self.bm25.remove(path)

    def rebuild(self, notes: dict):
        self.__init__()
        for note in notes.values():
            self.add_note(note)

    # --- links ----------------------------------------------------------------

    def resolve(self, name: str):
        return self._name_to_path.get(name.lower())

    def forward_links(self, path: str) -> set:
        note = self.notes.get(path)
        if not note:
            return set()
        out = set()
        for link in note.links:
            target = self.resolve(link)
            if target and target != path:
                out.add(target)
        return out

    def backlinks(self, path: str) -> set:
        note = self.notes.get(path)
        if not note:
            return set()
        sources = set()
        for name in self._names_for(note):
            sources |= self._link_sources.get(name, set())
        return sources - {path}

    # --- search ---------------------------------------------------------------

    def search(self, query: str, tag_filter: str | None = None, limit: int = 20,
               min_idf_coverage: float = 0.0) -> list:
        candidates = None
        if tag_filter:
            candidates = self.tag_index.get(tag_filter.lower(), set())
            if not candidates:
                return []
        return self.bm25.search(
            query, limit=limit, candidates=candidates, min_idf_coverage=min_idf_coverage
        )

    def get_related(self, path: str) -> list:
        if path not in self.notes:
            return []

        note = self.notes[path]
        scores: dict = defaultdict(float)

        for bl in self.backlinks(path):
            scores[bl] += 0.3
        for target in self.forward_links(path):
            scores[target] += 0.25
        for tag in note.tags:
            for other in self.tag_index.get(tag.lower(), set()):
                if other != path:
                    scores[other] += 0.2
        if note.folder:
            for other in self.folder_index.get(note.folder, set()):
                if other != path:
                    scores[other] += 0.1

        if not scores:
            return []
        max_score = max(scores.values())
        results = [(p, round(s / max_score, 2)) for p, s in scores.items()]
        results.sort(key=lambda x: (-x[1], x[0]))
        return results[:15]

    # --- compatibility --------------------------------------------------------

    @property
    def backlinks_map(self) -> dict:
        return {p: self.backlinks(p) for p in self.notes}


class Vault:
    """Markdown vault with a keyword index and an optional semantic index.

    The semantic index is lazy in two stages: the store object is created
    without opening Chroma, and Chroma is only opened when something actually
    searches or writes. A session-start hook that just reads markdown therefore
    never loads the vector index at all.
    """

    def __init__(self, vault_path: str, with_vector: bool = True):
        self.root = Path(vault_path).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = Index()
        self._vector_ready = False
        self._with_vector = with_vector and vector_enabled()
        # Hold our own reference rather than reading a module global on every
        # access: a Vault's semantic index must not be swapped out from under
        # it by an unrelated init elsewhere in the process.
        self._vector_store = init_vector_db(str(self.root)) if self._with_vector else None
        self._load_all()
        self._fingerprint = self._disk_fingerprint()

    # --- vector plumbing ------------------------------------------------------

    @property
    def vector(self):
        """The semantic store, or None when disabled."""
        return self._vector_store

    def ensure_vector_synced(self):
        """Bring the semantic index up to date. Call before semantic search.

        Kept out of ``__init__`` on purpose — building a Vault must stay cheap
        enough for a hook to do it on every session start.
        """
        if not self._with_vector:
            return
        store = self.vector
        # A store that rebuilt itself mid-write holds only whatever that write
        # touched, so a cached "ready" flag must not suppress the resync.
        if store is not None and getattr(store, "needs_full_sync", False):
            self._vector_ready = False
        if self._vector_ready:
            return
        self.sync_vector()
        self._vector_ready = True

    def sync_vector(self) -> dict:
        store = self.vector
        if store is None:
            return {"ok": False, "reason": "vector disabled"}
        docs = {
            path: (note.folder, note.title, note.content, note.tags)
            for path, note in self.index.notes.items()
        }
        return store.sync(docs)

    # --- loading --------------------------------------------------------------

    def _disk_fingerprint(self):
        """Cheap staleness signal: (path, mtime, size) for every note.

        One directory walk, where the previous version walked twice per check.
        """
        out = {}
        for f in self.root.rglob("*.md"):
            try:
                st = f.stat()
            except OSError:
                continue
            out[f.relative_to(self.root).as_posix()] = (st.st_mtime, st.st_size)
        return out

    def refresh_if_stale(self):
        current = self._disk_fingerprint()
        if current == self._fingerprint:
            return False

        added = current.keys() - self._fingerprint.keys()
        removed = self._fingerprint.keys() - current.keys()
        changed = {p for p in current.keys() & self._fingerprint.keys()
                   if current[p] != self._fingerprint[p]}

        for path in removed:
            self.index.remove_note(path)
        for path in added | changed:
            try:
                raw = (self.root / path).read_text(encoding="utf-8")
            except OSError:
                continue
            self.index.add_note(parse_note(path, raw))

        self._fingerprint = current
        # The semantic index is now behind; the next semantic search re-syncs.
        self._vector_ready = False
        return True

    def _load_all(self):
        notes = {}
        for md_file in self.root.rglob("*.md"):
            # as_posix(): on Windows str() yields backslashes, and every consumer
            # of these keys (Note.folder, is_session_path, project prefixes in
            # server.py and ui.py) matches on a literal "/". Without this the
            # whole product silently finds nothing on Windows.
            rel = md_file.relative_to(self.root).as_posix()
            try:
                raw = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            notes[rel] = parse_note(rel, raw)
        self.index.rebuild(notes)

    # --- reads ----------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """Resolve a vault-relative path, refusing anything that escapes the vault.

        Every caller of this class is untrusted. ``kyp_delete`` and ``kyp_write``
        take a path chosen by the agent — steerable by prompt injection through
        any note or file it reads — and the web UI exposes the same read, write
        and delete operations over HTTP. Without this check ``../../.ssh/id_rsa``
        or an absolute path passes straight through to ``unlink()``.

        Resolving also collapses symlinks, so a note symlinked outside the vault
        is rejected rather than followed.
        """
        candidate = Path(path)
        if candidate.is_absolute() or candidate.drive or candidate.root:
            raise ValueError(f"path must be relative to the vault: {path!r}")
        full = (self.root / candidate).resolve()
        if full != self.root and self.root not in full.parents:
            raise ValueError(f"path escapes the vault: {path!r}")
        return full

    def _safe_resolve(self, path: str):
        """``_resolve`` for read paths: returns None instead of raising."""
        try:
            return self._resolve(path)
        except ValueError as e:
            print(f"[kyp-mem] rejected unsafe path: {e}", file=sys.stderr)
            return None

    def list_tree(self, path: str = "") -> dict:
        base = self._safe_resolve(path) if path else self.root
        if base is None or not base.exists():
            return {"folders": [], "notes": []}
        folders = sorted(d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith("."))
        notes = sorted(f.name for f in base.iterdir() if f.is_file() and f.suffix == ".md")
        return {"folders": folders, "notes": notes}

    def read(self, path: str):
        full = self._safe_resolve(path)
        if full is None or not full.exists() or not full.is_file():
            return None
        raw = full.read_text(encoding="utf-8")
        return parse_note(path, raw)

    # --- writes ---------------------------------------------------------------

    def write_note(self, path: str, content: str, tags: list | None = None,
                   properties: dict | None = None):
        # Writes raise rather than silently no-op: creating a file outside the
        # vault is never a recoverable situation the caller should ignore.
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now().strftime("%Y-%m-%d")
        existing = self.read(path)
        created = existing.created if existing else now

        note = Note(
            path=path,
            title=Path(path).stem,
            content=content,
            tags=tags or [],
            properties=properties or {},
            created=created,
            updated=now,
        )

        full.write_text(serialize_note(note), encoding="utf-8")

        # Re-parse from what we just wrote so the index matches disk exactly
        # (title is derived from a leading "# " heading, not the filename).
        parsed = parse_note(path, full.read_text(encoding="utf-8"))
        self.index.add_note(parsed)
        try:
            st = full.stat()
            self._fingerprint[path] = (st.st_mtime, st.st_size)
        except OSError:
            pass

        store = self.vector
        if store is not None and store.is_connected():
            # Only touch the semantic index if it is already open. Otherwise
            # the next semantic search picks this note up during its sync,
            # which keeps writes off the slow path.
            store.upsert_note(path, parsed.folder, parsed.title, parsed.content, parsed.tags)
        else:
            self._vector_ready = False

    def delete(self, path: str) -> bool:
        full = self._safe_resolve(path)
        if full is None or not full.exists() or not full.is_file():
            return False
        full.unlink()
        # `self.root in parent.parents` keeps the climb inside the vault even if
        # the containment check above is ever weakened.
        parent = full.parent
        while parent != self.root and self.root in parent.parents and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

        self.index.remove_note(path)
        self._fingerprint.pop(path, None)

        store = self.vector
        if store is not None and store.is_connected():
            store.delete_note(path)
        else:
            self._vector_ready = False
        return True

    # --- search ---------------------------------------------------------------

    def search(
        self,
        query: str,
        tag: str | None = None,
        limit: int = 10,
        semantic: bool = True,
        project: str | None = None,
    ) -> list:
        """Hybrid keyword + semantic search over every note.

        Falls back cleanly to keyword-only when the semantic index is disabled
        or unavailable, so search never hard-fails on a broken vector store.
        """
        keyword_hits = self.index.search(
            query,
            tag_filter=tag,
            limit=max(limit * 3, limit),
            min_idf_coverage=MIN_KEYWORD_IDF_COVERAGE,
        )

        keyword_paths = {p for p, _ in keyword_hits}

        vector_hits = []
        if semantic and self._with_vector:
            try:
                self.ensure_vector_synced()
                store = self.vector
                if store is not None:
                    # Fetch at the lenient floor, then require the strict floor
                    # only for notes the keyword index did not also find.
                    raw = store.search(
                        query,
                        project=project,
                        n_results=max(limit * 3, limit),
                        min_similarity=SEMANTIC_FLOOR_CORROBORATED,
                    )
                    vector_hits = [
                        h for h in raw
                        if h.similarity >= SEMANTIC_FLOOR_ALONE or h.doc_path in keyword_paths
                    ]
                    if tag:
                        allowed = self.index.tag_index.get(tag.lower(), set())
                        vector_hits = [h for h in vector_hits if h.doc_path in allowed]
                    vector_hits = vector_hits[:max(limit * 2, limit)]
            except Exception:
                vector_hits = []

        return hybrid_search(
            query,
            keyword_hits,
            vector_hits,
            note_lookup=self.index.notes.get,
            limit=limit,
        )

    def search_sessions(self, query: str, project: str | None = None, limit: int = 5,
                        min_similarity: float | None = None):
        """Semantic search restricted to session notes."""
        if not self._with_vector:
            return []
        self.ensure_vector_synced()
        store = self.vector
        if store is None:
            return []
        kwargs = {"project": project, "n_results": limit, "sessions_only": True}
        if min_similarity is not None:
            kwargs["min_similarity"] = min_similarity
        return store.search(query, **kwargs)

    # --- derived views --------------------------------------------------------

    def get_tags(self) -> dict:
        return {tag: len(paths) for tag, paths in sorted(self.index.tag_index.items())}

    def get_notes_by_tag(self, tag: str) -> list:
        return sorted(self.index.tag_index.get(tag.lower(), set()))

    def get_related(self, path: str) -> list:
        return self.index.get_related(path)

    def get_backlinks(self, path: str) -> list:
        return sorted(self.index.backlinks(path))

    def get_recent(self, limit: int = 10) -> list:
        notes = list(self.index.notes.values())
        notes.sort(key=lambda n: n.updated or n.created or "", reverse=True)
        return notes[:limit]

    def get_full_tree(self) -> dict:
        tree = {"name": "vault", "type": "folder", "children": []}
        for path in sorted(self.index.notes):
            parts = Path(path).parts
            current = tree
            for part in parts[:-1]:
                existing = next(
                    (c for c in current["children"] if c["type"] == "folder" and c["name"] == part),
                    None,
                )
                if not existing:
                    existing = {"name": part, "type": "folder", "children": []}
                    current["children"].append(existing)
                current = existing
            note = self.index.notes[path]
            current["children"].append(
                {"name": parts[-1], "type": "note", "path": path, "tags": note.tags}
            )
        return tree

    def get_stats(self) -> dict:
        all_tags = set()
        all_links = 0
        for note in self.index.notes.values():
            all_tags.update(note.tags)
            all_links += len(note.links)
        return {
            "notes": len(self.index.notes),
            "folders": len(self.index.folder_index),
            "tags": len(all_tags),
            "links": all_links,
            "backlinks": sum(len(self.index.backlinks(p)) for p in self.index.notes),
        }
