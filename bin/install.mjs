#!/usr/bin/env node

import { spawnSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: root,
    stdio: options.stdio ?? "ignore",
    env: {
      ...process.env,
      PIP_DISABLE_PIP_VERSION_CHECK: "1",
    },
  });
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

if (process.env.KYP_MEM_SKIP_PYTHON_INSTALL === "1") {
  process.exit(0);
}

const python = findPython();

if (!python) {
  console.log("  \x1b[33m!\x1b[0m Python 3 was not found.");
  console.log("  \x1b[33m!\x1b[0m Install Python 3.10+ and run: python3 -m pip install --user .");
  process.exit(0);
}

const [pythonCommand, pythonPrefixArgs] = python;

console.log("  Installing kyp-mem Python package...");

const result = run(
  pythonCommand,
  [...pythonPrefixArgs, "-m", "pip", "install", "--user", "."],
  { stdio: "inherit" },
);

if (result.status === 0) {
  console.log("  \x1b[32m✓\x1b[0m kyp-mem installed successfully");
} else {
  console.log("  \x1b[33m!\x1b[0m Could not auto-install the Python package.");
  console.log(`  \x1b[33m!\x1b[0m Run manually from ${root}:`);
  console.log("    python3 -m pip install --user .");
}
