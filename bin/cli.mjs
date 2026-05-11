#!/usr/bin/env node

import { spawnSync } from "child_process";
import { delimiter, dirname, resolve } from "path";
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
