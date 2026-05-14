"""KYP-MEM web UI — interactive interface for browsing the vault."""

import webbrowser
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from .config import get_vault_path
from .vault import Vault


def create_app(vault_path: str = None) -> FastAPI:
    vault_path = vault_path or get_vault_path()
    vault = Vault(vault_path)
    app = FastAPI(title="KYP-MEM")

    @app.get("/")
    def index():
        html_path = Path(__file__).parent / "static" / "index.html"
        return HTMLResponse(html_path.read_text())

    @app.get("/api/tree")
    def tree():
        return JSONResponse(vault.get_full_tree())

    @app.get("/api/stats")
    def stats():
        return JSONResponse(vault.get_stats())

    @app.get("/api/note/{path:path}")
    def read_note(path: str):
        note = vault.read(path)
        if not note:
            return JSONResponse({"error": "Not found"}, 404)
        backlinks = vault.get_backlinks(path)
        related = vault.get_related(path)

        backlink_details = []
        for bl_path in backlinks:
            bl_note = vault.index.notes.get(bl_path)
            context = ""
            if bl_note:
                for line in bl_note.content.split("\n"):
                    if note.title.lower() in line.lower() or Path(path).stem.lower() in line.lower():
                        context = line.strip()[:150]
                        break
            backlink_details.append({
                "path": bl_path,
                "title": bl_note.title if bl_note else bl_path,
                "context": context,
            })

        unlinked = []
        stem = Path(path).stem.lower()
        title_lower = note.title.lower()
        for other_path, other_note in vault.index.notes.items():
            if other_path == path or other_path in backlinks:
                continue
            text = other_note.content.lower()
            if stem in text or title_lower in text:
                for line in other_note.content.split("\n"):
                    if stem in line.lower() or title_lower in line.lower():
                        ctx = line.strip()[:150]
                        break
                else:
                    ctx = ""
                unlinked.append({"path": other_path, "title": other_note.title, "context": ctx})

        return JSONResponse({
            "path": note.path,
            "title": note.title,
            "content": note.content,
            "tags": note.tags,
            "properties": note.properties,
            "created": note.created,
            "updated": note.updated,
            "links": note.links,
            "backlinks": backlink_details,
            "unlinked": unlinked[:10],
            "related": [{"path": p, "score": s, "title": vault.index.notes[p].title if p in vault.index.notes else p} for p, s in related],
        })

    @app.get("/api/search")
    def search(q: str = "", tag: str = ""):
        if not q and not tag:
            return JSONResponse([])
        if not q and tag:
            paths = vault.get_notes_by_tag(tag)
            return JSONResponse([
                {"path": p, "score": 1.0, "snippet": "", "title": vault.index.notes[p].title if p in vault.index.notes else p}
                for p in paths
            ])
        results = vault.search(q, tag or None)
        return JSONResponse([
            {"path": path, "score": score, "snippet": snippet, "title": vault.index.notes[path].title if path in vault.index.notes else path}
            for path, score, snippet in results
        ])

    @app.get("/api/tags")
    def tags():
        return JSONResponse(vault.get_tags())

    @app.get("/api/recent")
    def recent(limit: int = 10):
        notes = vault.get_recent(limit)
        return JSONResponse([
            {"path": n.path, "title": n.title, "updated": n.updated, "created": n.created, "tags": n.tags}
            for n in notes
        ])

    @app.post("/api/note/{path:path}")
    async def save_note(path: str, request: Request):
        body = await request.json()
        content = body.get("content", "")
        tags = body.get("tags", [])
        props = body.get("properties", {})
        if not path.endswith(".md"):
            path += ".md"
        vault.write_note(path, content, tags, props)
        return JSONResponse({"ok": True, "path": path})

    @app.post("/api/sessions/create")
    async def create_session(request: Request):
        body = await request.json()
        project = body.get("project", "").strip()
        summary = body.get("summary", "").strip()
        if not project:
            return JSONResponse({"error": "Project name required"}, 400)
        session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        content = (
            f"# Session {session_id}\n\n"
            f"**Project:** {project}\n\n"
            f"## Summary\n{summary}\n\n"
            f"## INVESTIGATED\n\n\n"
            f"## LEARNED\n\n\n"
            f"## COMPLETED\n\n\n"
            f"## NEXT STEPS\n\n"
        )
        tags = ["session", "manual", project.lower().replace(" ", "-")]
        path = f"{project}/Sessions/{session_id}.md"
        vault.write_note(path, content, tags, {})
        return JSONResponse({"ok": True, "path": path})

    @app.get("/api/sessions/search")
    def search_sessions(q: str = "", project: str = ""):
        from .vector import get_session_memory
        mem = get_session_memory()
        if not mem or not q:
            return JSONResponse([])
        results = mem.search_sessions(q, project=project or None, n_results=10)
        if not results or not results.get("ids") or not results["ids"][0]:
            return JSONResponse([])
        items = []
        for i, path in enumerate(results["ids"][0]):
            doc = results["documents"][0][i]
            dist = results["distances"][0][i]
            note = vault.index.notes.get(path)
            items.append({
                "path": path,
                "title": note.title if note else path,
                "distance": dist,
                "snippet": doc[:300],
            })
        return JSONResponse(items)

    @app.get("/api/sessions")
    def list_sessions(project: str = ""):
        sessions = {}
        for path, note in vault.index.notes.items():
            if "/Sessions/" not in path and not path.startswith("Sessions/"):
                continue
            parts = path.split("/")
            idx = parts.index("Sessions") if "Sessions" in parts else -1
            proj = "/".join(parts[:idx]) if idx > 0 else "(root)"
            if project and proj.lower() != project.lower():
                continue
            if proj not in sessions:
                sessions[proj] = []
            summary = ""
            lines = (note.content or "").split("\n")
            for i, line in enumerate(lines):
                if line.strip() == "## Summary":
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip() and not lines[j].startswith("#"):
                            summary = lines[j].strip()
                            break
                    break
            sessions[proj].append({
                "path": path,
                "title": note.title,
                "tags": note.tags,
                "created": note.created,
                "updated": note.updated,
                "summary": summary,
            })
        for proj in sessions:
            sessions[proj].sort(key=lambda s: s["path"], reverse=True)
        return JSONResponse(sessions)

    @app.get("/api/projects")
    def list_projects():
        projects = set()
        for path in vault.index.notes:
            parts = path.split("/")
            if len(parts) > 1:
                projects.add(parts[0])
        result = []
        for proj in sorted(projects):
            session_count = sum(1 for p in vault.index.notes if p.startswith(f"{proj}/Sessions/"))
            result.append({"name": proj, "session_count": session_count})
        return JSONResponse(result)

    @app.delete("/api/note/{path:path}")
    def delete_note(path: str):
        if vault.delete(path):
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "Not found"}, 404)

    @app.post("/api/projects/create")
    async def create_project(request: Request):
        body = await request.json()
        name = body.get("name", "").strip()
        overview = body.get("overview", "").strip()
        if not name:
            return JSONResponse({"error": "Project name required"}, 400)
        path = f"{name}/Knowledge.md"
        if vault.read(path):
            return JSONResponse({"error": "Project already exists"}, 409)
        content = (
            f"# {name}\n\n"
            f"## Overview\n{overview or '(Project description, goals, tech stack)'}\n\n"
            f"## Architecture\n(System design, key components, data flow)\n\n"
            f"## Bugs\n### Known\n\n### Fixed\n\n\n"
            f"## Improvements\n### Planned\n\n### Completed\n\n\n"
            f"## Key Decisions\n(Important architectural or design decisions)\n\n"
            f"## Notes\n(Miscellaneous project knowledge)\n"
        )
        tags = ["project", "knowledge", name.lower().replace(" ", "-")]
        vault.write_note(path, content, tags, {})
        return JSONResponse({"ok": True, "path": path})

    @app.post("/api/reload")
    def reload():
        vault._load_all()
        return JSONResponse({"ok": True, "stats": vault.get_stats()})

    return app


def start_ui(port: int = 3333, vault_path: str = None, open_browser: bool = True):
    app = create_app(vault_path)
    print(f"\033[36mKYP-MEM\033[0m UI -> http://localhost:{port}")
    if open_browser:
        webbrowser.open(f"http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
