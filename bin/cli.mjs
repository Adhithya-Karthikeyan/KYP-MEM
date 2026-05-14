#!/usr/bin/env node

import { spawnSync } from "child_process";
import { appendFileSync, mkdirSync } from "fs";
import { homedir } from "os";
import { delimiter, dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const args = process.argv.slice(2);
const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

const env = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH
    ? `${root}${delimiter}${process.env.PYTHONPATH}`
    : root,
};

function run(command, cmdArgs, stdio = "ignore") {
  return spawnSync(command, cmdArgs, { stdio, env });
}

function pythonCandidates() {
  if (process.env.KYP_MEM_PYTHON) {
    return [[process.env.KYP_MEM_PYTHON, []]];
  }

  const candidates = [
    ["python3", []],
    ["python", []],
  ];

  if (process.platform === "win32") {
    candidates.unshift(["py", ["-3"]]);
  }

  return candidates;
}

function findPython() {
  for (const [command, prefixArgs] of pythonCandidates()) {
    const result = run(command, [...prefixArgs, "--version"]);
    if (result.status === 0) {
      return [command, prefixArgs];
    }
  }

  return null;
}

// --- Hook fast path (pure Node, no Python startup) ---
if (args[0] === "hook") {
  const hookType = args[1];
  const sessionDir = join(homedir(), ".kyp-mem", "sessions");
  const sessionFile = join(sessionDir, "current.jsonl");

  const chunks = [];
  process.stdin.on("data", (chunk) => chunks.push(chunk));
  await new Promise((r) => process.stdin.on("end", r));
  const raw = Buffer.concat(chunks).toString();

  if (hookType === "user-prompt") {
    try {
      const data = JSON.parse(raw);
      const prompt = (data.prompt || "").trim();
      if (!prompt) process.exit(0);
      const entry = {
        ts: new Date().toISOString(),
        cwd: process.env.CLAUDE_PROJECT_DIR || process.cwd(),
        action: "prompt",
        prompt,
      };
      mkdirSync(sessionDir, { recursive: true });
      appendFileSync(sessionFile, JSON.stringify(entry) + "\n");
    } catch (_) {}
    process.exit(0);
  }

  if (hookType === "post-tool-use") {
    try {
      const data = JSON.parse(raw);
      const tool = data.tool_name || "";
      if (tool.includes("kyp-mem") || tool.includes("kyp_mem")) process.exit(0);

      const input = data.tool_input || {};
      const rawResp = data.tool_response || "";
      const resp = (typeof rawResp === "string" ? rawResp : JSON.stringify(rawResp)).slice(0, 2000);
      const entry = { ts: new Date().toISOString(), tool, cwd: process.cwd() };

      if (tool === "Edit" || tool === "Write") {
        entry.file = input.file_path || "";
        entry.action = tool === "Edit" ? "edit" : "create";
        if (input.old_string) entry.old_string = input.old_string.slice(0, 500);
        if (input.new_string) entry.new_string = input.new_string.slice(0, 500);
      } else if (tool === "Read") {
        entry.file = input.file_path || "";
        entry.action = "read";
        entry.content = resp;
      } else if (tool === "Bash") {
        entry.command = (input.command || "").slice(0, 300);
        entry.action = "command";
        entry.output = resp;
      } else {
        entry.action = "other";
        entry.detail = tool;
      }

      entry.response_chars = String(data.tool_response || "").length;
      mkdirSync(sessionDir, { recursive: true });
      appendFileSync(sessionFile, JSON.stringify(entry) + "\n");
    } catch (_) {
      // silent — hooks must never break the flow
    }
    process.exit(0);
  }

  if (hookType === "stop") {
    const py = findPython();
    if (py) {
      const [cmd, pre] = py;
      const r = run(cmd, [...pre, "-m", "kyp_mem.hooks", "stop"], "inherit");
      process.exit(r.status ?? 0);
    }
    process.exit(0);
  }

  if (hookType === "session-start") {
    const py = findPython();
    if (py) {
      const [cmd, pre] = py;
      const r = run(cmd, [...pre, "-m", "kyp_mem.cli", "hook", "session-start"], "inherit");
      process.exit(r.status ?? 0);
    }
    process.exit(0);
  }

  console.error("Unknown hook type:", hookType);
  process.exit(1);
}

const python = findPython();

if (python) {
  const [command, prefixArgs] = python;
  const result = run(command, [...prefixArgs, "-m", "kyp_mem.cli", ...args], "inherit");

  if (result.signal) {
    process.kill(process.pid, result.signal);
  }

  process.exit(result.status ?? 1);
}

console.error("");
console.error("  \x1b[31mError:\x1b[0m Python 3 was not found.");
console.error("");
console.error("  Install Python 3.10+ and rerun this command.");
console.error("");
process.exit(1);
