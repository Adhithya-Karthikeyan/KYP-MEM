"""KYP-MEM session hooks — compile captured tool activity into vault notes."""

import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

SESSION_DIR = Path.home() / ".kyp-mem" / "sessions"
CURRENT_SESSION = SESSION_DIR / "current.jsonl"

MIN_ACTIONS = 5
CHARS_PER_TOKEN = 4

COMMAND_OUTPUT_ESTIMATES = {
    "search": 2000,
    "explore": 1000,
    "read_cmd": 3000,
    "git_inspect": 3000,
    "test": 2000,
    "build": 500,
    "run": 200,
    "git_write": 200,
    "api_test": 1000,
    "other": 300,
}


def _load_token_stats():
    from .config import STATS_FILE
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"sessions": [], "injections": []}


def _save_token_stats(stats):
    from .config import STATS_FILE
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(stats, indent=2) + "\n")


def _record_session_tokens(session_id, project, exploration_tokens,
                           files_read_count, files_read_chars,
                           commands_run, commands_chars,
                           files_edited, files_created):
    stats = _load_token_stats()
    stats["sessions"].append({
        "id": session_id,
        "project": project,
        "ts": datetime.now().isoformat(),
        "exploration_tokens": exploration_tokens,
        "files_read": files_read_count,
        "files_read_chars": files_read_chars,
        "commands_run": commands_run,
        "commands_chars": commands_chars,
        "files_edited": files_edited,
        "files_created": files_created,
    })
    _save_token_stats(stats)


def _record_injection(project, chars):
    stats = _load_token_stats()
    stats["injections"].append({
        "ts": datetime.now().isoformat(),
        "project": project,
        "chars": chars,
        "tokens": chars // CHARS_PER_TOKEN,
    })
    _save_token_stats(stats)


def _is_subprocess():
    return os.environ.get("KYP_MEM_SUMMARIZING") == "1"


def handle_session_start():
    """Inject recent session memory into the conversation at session start."""
    sys.stdin.read()
    if _is_subprocess():
        return

    cwd = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    project_name = Path(cwd).name

    try:
        from .config import get_vault_path
        from .vault import Vault

        vault = Vault(get_vault_path())

        project_notes = [p for p in vault.index.notes if p.startswith(f"{project_name}/")]
        if not project_notes:
            return

        sessions = sorted(
            (p for p in project_notes if "/Sessions/" in p),
            reverse=True,
        )[:3]
        if not sessions:
            return

        parts = [f"# [kyp-mem] {project_name} — Recent Sessions"]
        parts.append(f"Use `kyp_search` or `kyp_project_context` for architecture/project knowledge on demand.")
        parts.append("")

        parts.append(f"## Last {len(sessions)} Sessions")
        for sp in sessions:
            note = vault.read(sp)
            if not note:
                continue
            parts.append(f"### {note.title}")
            content = note.content
            timeline_idx = content.find("## TIMELINE")
            if timeline_idx < 0:
                timeline_idx = content.find("## Timeline")
            if timeline_idx > 0:
                content = content[:timeline_idx].strip()
            parts.append(content)
            parts.append("")

        output = "\n".join(parts)
        try:
            _record_injection(project_name, len(output))
        except Exception:
            pass
        print(output)
    except Exception:
        pass


def handle_user_prompt():
    raw = sys.stdin.read().strip()
    if not raw or _is_subprocess():
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
    if not raw or _is_subprocess():
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

    tool_response = data.get("tool_response", "")
    if isinstance(tool_response, str):
        resp_str = tool_response
    elif tool_response:
        resp_str = json.dumps(tool_response)
    else:
        resp_str = ""
    response_chars = len(resp_str)
    resp_truncated = resp_str[:2000]

    if tool_name == "Edit":
        entry["action"] = "edit"
        entry["file"] = tool_input.get("file_path", "")
        old_s = tool_input.get("old_string", "")
        new_s = tool_input.get("new_string", "")
        if old_s:
            entry["old_string"] = old_s[:500]
        if new_s:
            entry["new_string"] = new_s[:500]
    elif tool_name == "Write":
        entry["action"] = "create"
        entry["file"] = tool_input.get("file_path", "")
    elif tool_name == "Read":
        entry["action"] = "read"
        entry["file"] = tool_input.get("file_path", "")
        if response_chars == 0:
            try:
                response_chars = Path(tool_input.get("file_path", "")).stat().st_size
            except OSError:
                pass
        entry["response_chars"] = response_chars
        entry["content"] = resp_truncated
    elif tool_name == "Bash":
        entry["action"] = "command"
        entry["command"] = tool_input.get("command", "")
        entry["response_chars"] = response_chars
        entry["output"] = resp_truncated
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
    """Use Claude CLI to rewrite session sections — uses existing Claude Code auth."""
    try:
        import shutil
        claude_bin = shutil.which("claude")
        if not claude_bin:
            return None

        from .config import get_session_model
        model = get_session_model()

        prompt = f"""Rewrite this raw coding session into a structured summary. A future AI agent reads this to pick up where you left off — be precise and technical.

You have: user prompts (the objectives), a timeline of file edits/reads/commands with their actual content and output. Synthesize into a dense, specific narrative.

## Format rules

- **Summary**: 1-2 sentences. State what was done and the outcome. Include error messages, feature names, or bug descriptions verbatim. Example: 'Debugged and fixed "Unknown hook type: session-start" error in kyp-mem; cleaned repository of session-specific files and prepared for release'
- **INVESTIGATED**: One dense paragraph (not bullets). List specific files, paths, and systems examined with semicolons. Include full relative paths and module names. Example: 'Global and project-level Claude Code settings.json; kyp-mem Python CLI source (cli.py, hooks.py); installed Node.js wrapper at /opt/homebrew/lib/node_modules/kyp-mem/bin/cli.mjs; hook dispatcher implementation; git commit history'
- **LEARNED**: One dense paragraph (not bullets). State technical insights with specifics — what was discovered, why it matters, root causes. Include version numbers, commit hashes, config values, error messages. Example: 'kyp-mem uses a Node.js wrapper with a "hook fast path" dispatcher that only handled 3 hook types (user-prompt, post-tool-use, stop); session-start was missing despite being implemented in Python backend'
- **COMPLETED**: One dense paragraph (not bullets). List concrete deliverables with specifics — file names modified, features added, tests passed, counts, commit hashes. Use semicolons to separate items. Example: 'Fixed .gitignore to exclude session-specific files (CLAUDE.md, PLAN-ui-rewrite.md, templates/); removed 3 tracked files from git history; committed cleanup to main (commit f0b114e: 4 files changed, 626 deletions)'
- **NEXT STEPS**: One dense paragraph (not bullets). Concrete actionable items for the next session. Example: 'Push commit f0b114e to GitHub; publish 0.5.1 release to npm with session-start hook support'

## Critical rules
- ALWAYS include specific file names, paths, commit hashes, error messages, and counts
- Write dense paragraphs with semicolons, NOT bullet lists
- Never be vague: "Fixed 3 files" is bad, "Fixed .gitignore, cli.mjs, and hooks.py" is good
- If a commit hash appears in the timeline, include it
- Keep each section to one paragraph max

Return ONLY this format (no preamble):

## Summary
<1-2 sentences>

## INVESTIGATED
<one paragraph>

## LEARNED
<one paragraph>

## COMPLETED
<one paragraph>

## NEXT STEPS
<one paragraph>

Raw session data:
{raw_note}"""

        result = subprocess.run(
            [claude_bin, "-p", prompt, "--max-turns", "1", "--model", model],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
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
    events = []

    for e in entries:
        ts_raw = e.get("ts", "")
        ts = ts_raw[11:19] if len(ts_raw) >= 19 else ""
        action = e.get("action", "")

        if action == "prompt":
            prompts.append({"ts": ts, "text": e.get("prompt", "")})
            events.append({"ts": ts, "type": "prompt", "text": e.get("prompt", "")[:500]})
        elif action == "read":
            fp = e.get("file", "")
            files_read.add(fp)
            content = e.get("content", "")
            events.append({"ts": ts, "type": "read", "file": fp, "content": content[:1000] if content else ""})
        elif action == "edit":
            fp = e.get("file", "")
            files_edited.add(fp)
            events.append({
                "ts": ts, "type": "edit", "file": fp,
                "old": e.get("old_string", "")[:300],
                "new": e.get("new_string", "")[:300],
            })
        elif action == "create":
            fp = e.get("file", "")
            files_created.add(fp)
            events.append({"ts": ts, "type": "create", "file": fp})
        elif action == "command":
            cmd = e.get("command", "")
            output = e.get("output", "")
            commands.append(cmd)
            events.append({"ts": ts, "type": "command", "cmd": cmd[:300], "output": output[:1000] if output else ""})

    commands_classified = [_classify_command(cmd) for cmd in commands]

    # Build rich context for Sonnet — actual content, not just filenames
    raw_parts = []

    if prompts:
        raw_parts.append("## USER PROMPTS (the objectives)")
        for p in prompts:
            raw_parts.append(f"[{p['ts']}] {p['text'][:500]}")
        raw_parts.append("")

    raw_parts.append("## SESSION EVENTS (chronological, with content)")
    for ev in events:
        if ev["type"] == "prompt":
            raw_parts.append(f"\n### [{ev['ts']}] User asked:")
            raw_parts.append(ev["text"])
        elif ev["type"] == "read":
            raw_parts.append(f"\n### [{ev['ts']}] Read `{ev['file']}`")
            if ev.get("content"):
                raw_parts.append(f"```\n{ev['content']}\n```")
        elif ev["type"] == "edit":
            raw_parts.append(f"\n### [{ev['ts']}] Edited `{ev['file']}`")
            if ev.get("old"):
                raw_parts.append(f"Replaced:\n```\n{ev['old']}\n```")
            if ev.get("new"):
                raw_parts.append(f"With:\n```\n{ev['new']}\n```")
        elif ev["type"] == "create":
            raw_parts.append(f"\n### [{ev['ts']}] Created `{ev['file']}`")
        elif ev["type"] == "command":
            raw_parts.append(f"\n### [{ev['ts']}] Ran: `{ev['cmd']}`")
            if ev.get("output"):
                raw_parts.append(f"Output:\n```\n{ev['output']}\n```")

    raw_parts.append("")
    raw_parts.append("## FILES MODIFIED")
    for fp in sorted(files_edited):
        raw_parts.append(f"- {_relative_path(fp, project_dir)}")
    raw_parts.append("")
    raw_parts.append("## FILES CREATED")
    for fp in sorted(files_created):
        raw_parts.append(f"- {_relative_path(fp, project_dir)}")
    raw_parts.append("")
    raw_parts.append("## FILES READ")
    for fp in sorted(files_read):
        raw_parts.append(f"- {_relative_path(fp, project_dir)}")

    # Keep timeline for backward compat in case summarization fails
    timeline = []
    for ev in events:
        if ev["type"] == "prompt":
            timeline.append(f"  {ev['ts']} — Prompt: {ev['text'][:60]}...")
        elif ev["type"] == "read":
            timeline.append(f"  {ev['ts']} — Read `{Path(ev['file']).name}`")
        elif ev["type"] == "edit":
            timeline.append(f"  {ev['ts']} — Edit `{Path(ev['file']).name}`")
        elif ev["type"] == "create":
            timeline.append(f"  {ev['ts']} — Write `{Path(ev['file']).name}`")
        elif ev["type"] == "command":
            short = ev["cmd"][:80] + "..." if len(ev["cmd"]) > 80 else ev["cmd"]
            timeline.append(f"  {ev['ts']} — `{short}`")

    investigated = _build_investigated(files_read, commands_classified, project_dir)
    learned = _build_learned(files_read, files_edited, files_created, commands_classified, project_dir)
    completed = _build_completed(files_edited, files_created, commands_classified, project_dir)
    next_steps = _build_next_steps(files_edited, files_created, commands_classified)

    if timeline:
        raw_parts.append("")
        raw_parts.append("## TIMELINE (what happened, chronological)")
        for line in timeline[:50]:
            raw_parts.append(line)

    raw_note = "\n".join(raw_parts)

    # Compute exploration tokens from captured response sizes
    files_read_chars = 0
    commands_chars = 0
    files_read_count = len(files_read)
    commands_run_count = len(commands)

    for e in entries:
        rc = e.get("response_chars", 0)
        if e.get("action") == "read":
            if rc > 0:
                files_read_chars += rc
            else:
                try:
                    files_read_chars += Path(e.get("file", "")).stat().st_size
                except OSError:
                    pass
        elif e.get("action") == "command":
            if rc > 0:
                commands_chars += rc
            else:
                cls, _ = _classify_command(e.get("command", ""))
                commands_chars += COMMAND_OUTPUT_ESTIMATES.get(cls, 300)

    exploration_chars = files_read_chars + commands_chars
    exploration_tokens = exploration_chars // CHARS_PER_TOKEN

    try:
        _record_session_tokens(
            session_id, project_name, exploration_tokens,
            files_read_count, files_read_chars,
            commands_run_count, commands_chars,
            len(files_edited), len(files_created),
        )
    except Exception:
        pass

    # Delete session file BEFORE summarization so the spawned claude subprocess
    # doesn't pollute it via hooks writing back into current.jsonl
    CURRENT_SESSION.unlink(missing_ok=True)

    # Try Claude summarization, fall back to raw sections
    summarized = _summarize_with_claude(raw_note, project_name)

    parts = [f"# Session {session_id}", ""]
    parts.append(f"**Project:** `{project_dir}`")
    parts.append(f"**Actions:** {len(entries)} total, {len(write_actions)} substantive")
    parts.append(f"**Exploration:** ~{exploration_tokens:,} tokens ({files_read_count} reads, {commands_run_count} commands)")
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
        summary_items = []
        if files_edited:
            summary_items.append(f"Modified {len(files_edited)} file{'s' if len(files_edited) != 1 else ''}")
        if files_created:
            summary_items.append(f"Created {len(files_created)} file{'s' if len(files_created) != 1 else ''}")
        if commands:
            summary_items.append(f"Ran {len(commands)} command{'s' if len(commands) != 1 else ''}")
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
