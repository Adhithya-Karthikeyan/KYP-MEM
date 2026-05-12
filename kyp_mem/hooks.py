"""KYP-MEM session hooks — compile captured tool activity into vault notes."""

import sys
import json
from datetime import datetime
from pathlib import Path

SESSION_DIR = Path.home() / ".kyp-mem" / "sessions"
CURRENT_SESSION = SESSION_DIR / "current.jsonl"

MIN_ACTIONS = 3


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
    timeline = []

    for e in entries:
        ts_raw = e.get("ts", "")
        ts = ts_raw[11:19] if len(ts_raw) >= 19 else ""
        action = e.get("action", "")

        if action == "edit":
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

    parts = [f"# Session {session_id}", ""]
    parts.append(f"**Project:** `{project_dir}`")
    parts.append(f"**Actions:** {len(entries)} total, {len(write_actions)} substantive")
    parts.append("")

    if files_edited:
        parts.append("## Files Modified")
        for f in sorted(files_edited):
            parts.append(f"- `{f}`")
        parts.append("")

    if files_created:
        parts.append("## Files Created")
        for f in sorted(files_created):
            parts.append(f"- `{f}`")
        parts.append("")

    if commands:
        parts.append("## Commands Run")
        for cmd in commands[:25]:
            short = cmd[:120] + "..." if len(cmd) > 120 else cmd
            parts.append(f"- `{short}`")
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
    vault.write_note(f"Sessions/{session_id}.md", content, tags, {})

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
