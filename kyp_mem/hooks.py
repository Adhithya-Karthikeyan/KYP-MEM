"""KYP-MEM session hooks — compile captured tool activity into vault notes."""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

SESSION_DIR = Path.home() / ".kyp-mem" / "sessions"
CURRENT_SESSION = SESSION_DIR / "current.jsonl"

MIN_ACTIONS = 3


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

    summary_items = []
    if files_edited:
        summary_items.append(f"Modified {len(files_edited)} file{'s' if len(files_edited) != 1 else ''}")
    if files_created:
        summary_items.append(f"Created {len(files_created)} file{'s' if len(files_created) != 1 else ''}")
    if commands:
        summary_items.append(f"Ran {len(commands)} command{'s' if len(commands) != 1 else ''}")

    investigate_keywords = {'grep', 'find', 'cat', 'head', 'tail', 'less', 'ls', 'tree', 'rg', 'ag', 'fd', 'wc', 'diff'}
    investigated_cmds = []
    for cmd in commands:
        first_word = cmd.strip().split()[0] if cmd.strip() else ''
        if first_word in investigate_keywords:
            investigated_cmds.append(cmd)

    parts = [f"# Session {session_id}", ""]
    parts.append(f"**Project:** `{project_dir}`")
    parts.append(f"**Actions:** {len(entries)} total, {len(write_actions)} substantive")
    parts.append("")

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
    if investigated_cmds:
        for cmd in investigated_cmds[:15]:
            short = cmd[:120] + "..." if len(cmd) > 120 else cmd
            parts.append(f"- `{short}`")
    parts.append("")

    parts.append("## LEARNED")
    parts.append("")
    parts.append("")

    parts.append("## COMPLETED")
    if files_edited:
        for f in sorted(files_edited):
            parts.append(f"- Modified `{Path(f).name}`")
    if files_created:
        for f in sorted(files_created):
            parts.append(f"- Created `{Path(f).name}`")
    parts.append("")

    parts.append("## NEXT STEPS")
    parts.append("")
    parts.append("")

    if timeline:
        parts.append("## Timeline")
        for line in timeline[:40]:
            parts.append(line)
        if len(timeline) > 40:
            parts.append(f"  ... and {len(timeline) - 40} more actions")

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
