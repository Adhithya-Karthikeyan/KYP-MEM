"""Vault — markdown file storage with frontmatter, wikilinks, and indexing."""

import re
import json
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


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
    def __init__(self):
        self.notes: dict[str, Note] = {}
        self.backlinks: dict[str, set] = defaultdict(set)
        self.forward_links: dict[str, set] = defaultdict(set)
        self.tag_index: dict[str, set] = defaultdict(set)
        self._word_index: dict[str, set] = defaultdict(set)
        self._name_to_path: dict[str, str] = {}

    def rebuild(self, notes: dict[str, Note]):
        self.notes = notes
        self.backlinks = defaultdict(set)
        self.forward_links = defaultdict(set)
        self.tag_index = defaultdict(set)
        self._word_index = defaultdict(set)
        self._name_to_path = {}

        for path, note in notes.items():
            self._name_to_path[Path(path).stem.lower()] = path
            self._name_to_path[note.title.lower()] = path

        for path, note in notes.items():
            for link in note.links:
                target = self._name_to_path.get(link.lower())
                if target and target != path:
                    self.backlinks[target].add(path)
                    self.forward_links[path].add(target)

            for tag in note.tags:
                self.tag_index[tag.lower()].add(path)

            text = f"{note.title} {note.content} {' '.join(note.tags)}".lower()
            for word in set(re.findall(r"\w+", text)):
                self._word_index[word].add(path)

    def search(self, query: str, tag_filter: str = None) -> list[tuple[str, float, str]]:
        query_words = re.findall(r"\w+", query.lower())
        if not query_words:
            return []

        candidates = None
        for word in query_words:
            matching = set()
            for idx_word, paths in self._word_index.items():
                if word in idx_word:
                    matching |= paths
            candidates = matching if candidates is None else candidates & matching

        if not candidates:
            return []

        if tag_filter:
            candidates &= self.tag_index.get(tag_filter.lower(), set())

        results = []
        for path in candidates:
            note = self.notes[path]
            text = f"{note.title}\n{note.content}"
            score = sum(text.lower().count(w) for w in query_words) / max(len(text.split()), 1)

            snippet = ""
            lower_text = text.lower()
            for w in query_words:
                idx = lower_text.find(w)
                if idx >= 0:
                    start = max(0, idx - 50)
                    end = min(len(text), idx + 80)
                    snippet = "..." + text[start:end].strip() + "..."
                    break

            results.append((path, score, snippet))

        results.sort(key=lambda x: -x[1])
        return results[:20]

    def get_related(self, path: str) -> list[tuple[str, float]]:
        if path not in self.notes:
            return []

        note = self.notes[path]
        scores: dict[str, float] = defaultdict(float)

        for bl in self.backlinks.get(path, set()):
            scores[bl] += 0.3

        for link in note.links:
            target = self._name_to_path.get(link.lower())
            if target and target != path:
                scores[target] += 0.25

        for tag in note.tags:
            for other_path in self.tag_index.get(tag.lower(), set()):
                if other_path != path:
                    scores[other_path] += 0.2

        folder = note.folder
        if folder:
            for other_path, other_note in self.notes.items():
                if other_path != path and other_note.folder == folder:
                    scores[other_path] += 0.1

        if not scores:
            return []
        max_score = max(scores.values())
        results = [(p, round(s / max_score, 2)) for p, s in scores.items()]
        results.sort(key=lambda x: -x[1])
        return results[:15]


from .vector import init_vector_db, get_session_memory

class Vault:
    def __init__(self, vault_path: str):
        self.root = Path(vault_path).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = Index()
        init_vector_db(str(self.root))
        self._load_all()
        self._sync_vector_db()
        self._last_mtime = self._max_mtime()

    def _disk_note_paths(self) -> set[str]:
        return {str(f.relative_to(self.root)) for f in self.root.rglob("*.md")}

    def _max_mtime(self) -> float:
        mtimes = [f.stat().st_mtime for f in self.root.rglob("*.md")]
        return max(mtimes) if mtimes else 0.0

    def refresh_if_stale(self):
        current_mtime = self._max_mtime()
        paths_changed = self._disk_note_paths() != set(self.index.notes.keys())
        content_changed = current_mtime != self._last_mtime
        if paths_changed or content_changed:
            self._load_all()
            self._sync_vector_db()
            self._last_mtime = current_mtime

    def _sync_vector_db(self):
        mem = get_session_memory()
        for path, note in self.index.notes.items():
            if "/Sessions/" in path or path.startswith("Sessions/"):
                folder = note.folder
                mem.upsert_session(path, folder, note.content)

    def _load_all(self):
        notes = {}
        for md_file in self.root.rglob("*.md"):
            rel = str(md_file.relative_to(self.root))
            raw = md_file.read_text(encoding="utf-8")
            notes[rel] = parse_note(rel, raw)
        self.index.rebuild(notes)

    def list_tree(self, path: str = "") -> dict:
        base = self.root / path if path else self.root
        if not base.exists():
            return {"folders": [], "notes": []}
        folders = sorted(d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith("."))
        notes = sorted(f.name for f in base.iterdir() if f.is_file() and f.suffix == ".md")
        return {"folders": folders, "notes": notes}

    def read(self, path: str) -> Note | None:
        full = self.root / path
        if not full.exists():
            return None
        raw = full.read_text(encoding="utf-8")
        return parse_note(path, raw)

    def write_note(self, path: str, content: str, tags: list = None, properties: dict = None):
        full = self.root / path
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
        self._load_all()
        if "/Sessions/" in path or path.startswith("Sessions/"):
            get_session_memory().upsert_session(path, note.folder, note.content)

    def delete(self, path: str) -> bool:
        full = self.root / path
        if full.exists():
            full.unlink()
            # Clean up empty parent dirs
            parent = full.parent
            while parent != self.root and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            self._load_all()
            if "/Sessions/" in path or path.startswith("Sessions/"):
                get_session_memory().delete_session(path)
            return True
        return False

    def search(self, query: str, tag: str = None) -> list:
        return self.index.search(query, tag)

    def get_tags(self) -> dict[str, int]:
        return {tag: len(paths) for tag, paths in sorted(self.index.tag_index.items())}

    def get_notes_by_tag(self, tag: str) -> list[str]:
        return sorted(self.index.tag_index.get(tag.lower(), set()))

    def get_related(self, path: str) -> list[tuple[str, float]]:
        return self.index.get_related(path)

    def get_backlinks(self, path: str) -> list[str]:
        return sorted(self.index.backlinks.get(path, set()))

    def get_recent(self, limit: int = 10) -> list[Note]:
        notes = list(self.index.notes.values())
        notes.sort(key=lambda n: n.updated or n.created or "", reverse=True)
        return notes[:limit]

    def get_full_tree(self) -> dict:
        tree = {"name": "vault", "type": "folder", "children": []}
        for path in sorted(self.index.notes):
            parts = Path(path).parts
            current = tree
            for part in parts[:-1]:
                existing = next((c for c in current["children"] if c["type"] == "folder" and c["name"] == part), None)
                if not existing:
                    existing = {"name": part, "type": "folder", "children": []}
                    current["children"].append(existing)
                current = existing
            note = self.index.notes[path]
            current["children"].append({
                "name": parts[-1],
                "type": "note",
                "path": path,
                "tags": note.tags,
            })
        return tree

    def get_stats(self) -> dict:
        all_tags = set()
        all_links = 0
        for note in self.index.notes.values():
            all_tags.update(note.tags)
            all_links += len(note.links)
        return {
            "notes": len(self.index.notes),
            "folders": len(set(n.folder for n in self.index.notes.values() if n.folder)),
            "tags": len(all_tags),
            "links": all_links,
            "backlinks": sum(len(v) for v in self.index.backlinks.values()),
        }
