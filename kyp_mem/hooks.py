"""KYP-MEM session hooks — compile captured tool activity into vault notes."""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

SESSION_DIR = Path.home() / ".kyp-mem" / "sessions"
CURRENT_SESSION = SESSION_DIR / "current.jsonl"

MIN_ACTIONS = 3


def handle_session_start():
    """Inject project context into the conversation at session start."""
    sys.stdin.read()

    cwd = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    project_name = Path(cwd).name

    try:
        from .config import get_vault_path
        from .vault import Vault

        vault = Vault(get_vault_path())

        project_notes = [p for p in vault.index.notes if p.startswith(f"{project_name}/")]
        if not project_notes:
            return

        parts = [f"# [kyp-mem] {project_name} — Project Context"]
        parts.append(f"Vault: {get_vault_path()}")
        parts.append("")

        knowledge_path = f"{project_name}/Knowledge.md"
        knowledge = vault.read(knowledge_path)
        if knowledge:
            parts.append("## Knowledge")
            content = knowledge.content
            timeline_idx = content.find("## Timeline")
            if timeline_idx > 0:
                content = content[:timeline_idx].strip()
            if len(content) > 2000:
                parts.append(content[:2000] + "\n...")
            else:
                parts.append(content)
            parts.append("")

        other_notes = sorted(
            p for p in project_notes
            if "/Sessions/" not in p and p != knowledge_path
        )
        if other_notes:
            parts.append("## Project Notes")
            for p in other_notes:
                note = vault.index.notes.get(p)
                title = note.title if note else p
                tags = f" [{', '.join(note.tags)}]" if note and note.tags else ""
                parts.append(f"- {title} ({p}){tags}")
            parts.append("")

        sessions = sorted(
            (p for p in project_notes if "/Sessions/" in p),
            reverse=True,
        )[:3]
        if sessions:
            parts.append(f"## Recent Sessions (last {len(sessions)})")
            for sp in sessions:
                note = vault.read(sp)
                if not note:
                    continue
                parts.append(f"### {note.title}")
                content = note.content
                timeline_idx = content.find("## Timeline")
                if timeline_idx > 0:
                    content = content[:timeline_idx].strip()
                if len(content) > 300:
                    content = content[:300] + "..."
                parts.append(content)
                parts.append("")

        parts.append("Use `kyp_project_context` for full details. Use `kyp_session_search` to search past sessions.")

        print("\n".join(parts))
    except Exception:
        pass


def handle_user_prompt():
    raw = sys.stdin.read().strip()
    if not raw:
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    prompt = data.get("prompt", "").strip()
    if not prompt:
        return

    entry = {
        "ts": datetime.now().isoformat(),
        "cwd": os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()),
        "action": "prompt",
        "prompt": prompt,
    }

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    with open(CURRENT_SESSION, "a") as f:
        f.write(json.dumps(entry) + "\n")


def handle_post_tool_use():
    raw = sys.stdin.read().strip()
    if not raw:
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    entry = {"ts": datetime.now().isoformat()}

    cwd = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    entry["cwd"] = cwd

    if tool_name == "Edit":
        entry["action"] = "edit"
        entry["file"] = tool_input.get("file_path", "")
    elif tool_name == "Write":
        entry["action"] = "create"
        entry["file"] = tool_input.get("file_path", "")
    elif tool_name == "Read":
        entry["action"] = "read"
        entry["file"] = tool_input.get("file_path", "")
    elif tool_name == "Bash":
        entry["action"] = "command"
        entry["command"] = tool_input.get("command", "")
    else:
        return

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    with open(CURRENT_SESSION, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _relative_path(filepath, project_dir):
    try:
        return str(Path(filepath).relative_to(project_dir))
    except (ValueError, TypeError):
        return Path(filepath).name if filepath else ""


def _group_files_by_dir(filepaths, project_dir):
    groups = {}
    for fp in sorted(filepaths):
        rel = _relative_path(fp, project_dir)
        parent = str(Path(rel).parent)
        if parent == ".":
            parent = "(root)"
        groups.setdefault(parent, []).append(Path(rel).name)
    return groups


def _classify_command(cmd):
    if not cmd.strip():
        return "other", ""
    first = cmd.strip().split()[0]
    search_cmds = {'grep', 'rg', 'ag', 'ack'}
    explore_cmds = {'find', 'fd', 'ls', 'tree', 'du'}
    read_cmds = {'cat', 'head', 'tail', 'less', 'more', 'wc', 'file'}
    diff_cmds = {'diff', 'git diff', 'git log', 'git status', 'git show'}
    test_cmds = {'pytest', 'python -m pytest', 'npm test', 'jest', 'mocha', 'cargo test', 'go test'}
    build_cmds = {'npm run', 'npm install', 'pip install', 'make', 'cargo build', 'go build'}
    server_cmds = {'python3 -m', 'uvicorn', 'node', 'npm start', 'flask run'}
    git_cmds = {'git commit', 'git push', 'git add', 'git checkout', 'git branch', 'git merge', 'git stash'}

    if first in search_cmds or cmd.strip().startswith('git grep'):
        return "search", cmd
    if first in explore_cmds:
        return "explore", cmd
    if first in read_cmds:
        return "read_cmd", cmd
    for prefix in diff_cmds:
        if cmd.strip().startswith(prefix):
            return "git_inspect", cmd
    for prefix in test_cmds:
        if cmd.strip().startswith(prefix):
            return "test", cmd
    for prefix in build_cmds:
        if cmd.strip().startswith(prefix):
            return "build", cmd
    for prefix in git_cmds:
        if cmd.strip().startswith(prefix):
            return "git_write", cmd
    for prefix in server_cmds:
        if cmd.strip().startswith(prefix):
            return "run", cmd
    if first == 'curl':
        return "api_test", cmd
    return "other", cmd


def _build_investigated(files_read, commands_classified, project_dir):
    items = []
    seen_files = set()

    search_cmds = [cmd for cls, cmd in commands_classified if cls == "search"]
    for cmd in search_cmds[:8]:
        parts = cmd.strip().split()
        if len(parts) >= 2:
            pattern = None
            for i, p in enumerate(parts):
                if not p.startswith('-') and i > 0:
                    pattern = p.strip("'\"")
                    break
            if pattern:
                items.append(f"- Searched for `{pattern[:60]}`")
            else:
                items.append(f"- `{cmd[:100]}`")

    read_groups = _group_files_by_dir(files_read, project_dir)
    for dir_name, filenames in read_groups.items():
        unique = [f for f in filenames if f not in seen_files]
        seen_files.update(unique)
        if not unique:
            continue
        if len(unique) <= 3:
            items.append(f"- Read {', '.join(f'`{f}`' for f in unique)}")
        else:
            items.append(f"- Read {len(unique)} files in `{dir_name}/`")

    explore_cmds = [cmd for cls, cmd in commands_classified if cls == "explore"]
    if explore_cmds:
        items.append(f"- Explored project structure ({len(explore_cmds)} commands)")

    git_cmds = [cmd for cls, cmd in commands_classified if cls == "git_inspect"]
    if git_cmds:
        items.append(f"- Inspected git history/diff ({len(git_cmds)} commands)")

    api_cmds = [cmd for cls, cmd in commands_classified if cls == "api_test"]
    if api_cmds:
        items.append(f"- Tested API endpoints ({len(api_cmds)} requests)")

    return items


def _build_learned(files_read, files_edited, files_created, commands_classified, project_dir):
    items = []
    read_set = {Path(f).name for f in files_read}
    edit_set = {Path(f).name for f in files_edited}
    create_set = {Path(f).name for f in files_created}

    investigated_then_modified = read_set & edit_set
    if investigated_then_modified:
        names = sorted(investigated_then_modified)[:5]
        items.append(f"- Investigated and modified: {', '.join(f'`{n}`' for n in names)}")

    config_files = {f for f in (files_edited | files_created)
                    if any(f.endswith(ext) for ext in
                           ('.json', '.toml', '.yaml', '.yml', '.ini', '.cfg', '.env', '.conf'))}
    if config_files:
        items.append(f"- Configuration changes: {', '.join(f'`{Path(f).name}`' for f in sorted(config_files)[:4])}")

    test_cmds = [cmd for cls, cmd in commands_classified if cls == "test"]
    if test_cmds:
        items.append(f"- Ran tests ({len(test_cmds)} run{'s' if len(test_cmds) != 1 else ''})")

    new_only = create_set - read_set
    if new_only:
        items.append(f"- Created new files: {', '.join(f'`{n}`' for n in sorted(new_only)[:5])}")

    return items


def _build_completed(files_edited, files_created, commands_classified, project_dir):
    items = []

    edit_groups = _group_files_by_dir(files_edited, project_dir)
    for dir_name, filenames in edit_groups.items():
        if len(filenames) == 1:
            items.append(f"- Modified `{filenames[0]}`")
        else:
            items.append(f"- Modified {len(filenames)} files in `{dir_name}/`: {', '.join(f'`{f}`' for f in filenames[:5])}")

    create_groups = _group_files_by_dir(files_created, project_dir)
    for dir_name, filenames in create_groups.items():
        if len(filenames) == 1:
            items.append(f"- Created `{filenames[0]}`")
        else:
            items.append(f"- Created {len(filenames)} files in `{dir_name}/`")

    test_cmds = [cmd for cls, cmd in commands_classified if cls == "test"]
    if test_cmds:
        items.append(f"- Ran test suite")

    git_writes = [cmd for cls, cmd in commands_classified if cls == "git_write"]
    for cmd in git_writes:
        if 'commit' in cmd:
            items.append("- Committed changes to git")
            break
    for cmd in git_writes:
        if 'push' in cmd:
            items.append("- Pushed to remote")
            break

    build_cmds = [cmd for cls, cmd in commands_classified if cls == "build"]
    if build_cmds:
        items.append("- Ran build/install")

    run_cmds = [cmd for cls, cmd in commands_classified if cls == "run"]
    if run_cmds:
        items.append("- Started/tested server")

    return items


def _build_next_steps(files_edited, files_created, commands_classified):
    items = []

    git_writes = {cmd for cls, cmd in commands_classified if cls == "git_write"}
    has_commit = any('commit' in cmd for cmd in git_writes)
    has_push = any('push' in cmd for cmd in git_writes)

    all_changed = files_edited | files_created
    if all_changed and not has_commit:
        items.append("- Commit pending changes")
    if has_commit and not has_push:
        items.append("- Push committed changes to remote")

    test_cmds = [cmd for cls, cmd in commands_classified if cls == "test"]
    if not test_cmds and all_changed:
        items.append("- Run tests to verify changes")

    return items


def _summarize_with_claude(raw_note, project_name):
    """Use Claude to rewrite session sections in plain, human-readable language."""
    try:
        from .config import get_session_model
        import anthropic

        model = get_session_model()
        client = anthropic.Anthropic()

        prompt = f"""You are summarizing a coding session for the project "{project_name}".
Below is a raw session note with sections: Summary, INVESTIGATED, LEARNED, COMPLETED, NEXT STEPS.
The raw data contains file names, grep patterns, and command output — rewrite each section in plain, conversational English describing what was actually done and why.

Rules:
- Summary: 1-2 sentences describing what the session accomplished in plain words
- INVESTIGATED: Bullet points explaining what was analyzed or explored and why, not raw grep patterns or file paths
- LEARNED: Bullet points of insights or discoveries made during the session
- COMPLETED: Bullet points of concrete deliverables or changes made
- NEXT STEPS: Bullet points of what should be done next
- Keep each section concise (2-5 bullets max)
- Do NOT include raw command output, grep patterns, or technical file paths
- Write as if explaining to a teammate what you did today
- Return ONLY the rewritten sections in this exact format (no other text):

## Summary
<text>

## INVESTIGATED
- <item>

## LEARNED
- <item>

## COMPLETED
- <item>

## NEXT STEPS
- <item>

Raw session note:
{raw_note}"""

        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return None


def handle_stop():
    if not CURRENT_SESSION.exists():
        return

    text = CURRENT_SESSION.read_text().strip()
    if not text:
        CURRENT_SESSION.unlink(missing_ok=True)
        return

    entries = []
    for line in text.split("\n"):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not entries:
        CURRENT_SESSION.unlink(missing_ok=True)
        return

    write_actions = [e for e in entries if e.get("action") in ("edit", "create", "command")]
    if len(write_actions) < MIN_ACTIONS:
        CURRENT_SESSION.unlink(missing_ok=True)
        return

    project_dir = entries[0].get("cwd", "unknown")
    project_name = Path(project_dir).name
    session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    files_read = set()
    files_edited = set()
    files_created = set()
    commands = []
    prompts = []
    timeline = []

    for e in entries:
        ts_raw = e.get("ts", "")
        ts = ts_raw[11:19] if len(ts_raw) >= 19 else ""
        action = e.get("action", "")

        if action == "prompt":
            prompts.append({"ts": ts, "text": e.get("prompt", "")})
            timeline.append(f"  {ts} — Prompt: {e.get('prompt', '')[:60]}...")
        elif action == "read":
            fp = e.get("file", "")
            files_read.add(fp)
            timeline.append(f"  {ts} — Read `{Path(fp).name}`")
        elif action == "edit":
            fp = e.get("file", "")
            files_edited.add(fp)
            timeline.append(f"  {ts} — Edit `{Path(fp).name}`")
        elif action == "create":
            fp = e.get("file", "")
            files_created.add(fp)
            timeline.append(f"  {ts} — Write `{Path(fp).name}`")
        elif action == "command":
            cmd = e.get("command", "")
            commands.append(cmd)
            short = cmd[:80] + "..." if len(cmd) > 80 else cmd
            timeline.append(f"  {ts} — `{short}`")

    commands_classified = [_classify_command(cmd) for cmd in commands]

    summary_items = []
    if files_edited:
        summary_items.append(f"Modified {len(files_edited)} file{'s' if len(files_edited) != 1 else ''}")
    if files_created:
        summary_items.append(f"Created {len(files_created)} file{'s' if len(files_created) != 1 else ''}")
    if commands:
        summary_items.append(f"Ran {len(commands)} command{'s' if len(commands) != 1 else ''}")

    investigated = _build_investigated(files_read, commands_classified, project_dir)
    learned = _build_learned(files_read, files_edited, files_created, commands_classified, project_dir)
    completed = _build_completed(files_edited, files_created, commands_classified, project_dir)
    next_steps = _build_next_steps(files_edited, files_created, commands_classified)

    # Build raw note for Claude summarization
    raw_parts = []
    raw_parts.append("## Summary")
    raw_parts.append(", ".join(summary_items) + f" in `{project_name}`." if summary_items else "")
    raw_parts.append("")
    raw_parts.append("## INVESTIGATED")
    if investigated:
        raw_parts.extend(investigated)
    raw_parts.append("")
    raw_parts.append("## LEARNED")
    if learned:
        raw_parts.extend(learned)
    raw_parts.append("")
    raw_parts.append("## COMPLETED")
    if completed:
        raw_parts.extend(completed)
    raw_parts.append("")
    raw_parts.append("## NEXT STEPS")
    if next_steps:
        raw_parts.extend(next_steps)

    # Add prompts and timeline as context for Claude
    if prompts:
        raw_parts.append("")
        raw_parts.append("## PROMPTS (context)")
        for p in prompts:
            raw_parts.append(f"- [{p['ts']}] {p['text'][:200]}")
    if timeline:
        raw_parts.append("")
        raw_parts.append("## Timeline (context)")
        for line in timeline[:30]:
            raw_parts.append(line)

    raw_note = "\n".join(raw_parts)

    # Try Claude summarization, fall back to raw sections
    summarized = _summarize_with_claude(raw_note, project_name)

    parts = [f"# Session {session_id}", ""]
    parts.append(f"**Project:** `{project_dir}`")
    parts.append(f"**Actions:** {len(entries)} total, {len(write_actions)} substantive")
    parts.append("")

    if summarized:
        # Insert prompts section before the Claude-rewritten sections
        parts.append("## PROMPTS")
        if prompts:
            for i, p in enumerate(prompts, 1):
                parts.append(f"### {i}. [{p['ts']}]")
                parts.append(f"> {p['text']}")
                parts.append("")
        parts.append("")
        parts.append(summarized)
    else:
        parts.append("## Summary")
        parts.append(", ".join(summary_items) + f" in `{project_name}`." if summary_items else "")
        parts.append("")

        parts.append("## PROMPTS")
        if prompts:
            for i, p in enumerate(prompts, 1):
                parts.append(f"### {i}. [{p['ts']}]")
                parts.append(f"> {p['text']}")
                parts.append("")
        parts.append("")

        parts.append("## INVESTIGATED")
        if investigated:
            parts.extend(investigated)
        parts.append("")

        parts.append("## LEARNED")
        if learned:
            parts.extend(learned)
        parts.append("")

        parts.append("## COMPLETED")
        if completed:
            parts.extend(completed)
        parts.append("")

        parts.append("## NEXT STEPS")
        if next_steps:
            parts.extend(next_steps)
        parts.append("")

    content = "\n".join(parts)
    tags = ["session", "auto-captured", project_name]

    from .config import get_vault_path
    from .vault import Vault

    vault = Vault(get_vault_path())
    vault.write_note(f"{project_name}/Sessions/{session_id}.md", content, tags, {})

    CURRENT_SESSION.unlink(missing_ok=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        handle_stop()
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if "stop_reason" in data:
            handle_stop()


if __name__ == "__main__":
    main()
