#!/usr/bin/env node

import { spawnSync } from "child_process";
import { mkdirSync } from "fs";
import { homedir } from "os";
import { fileURLToPath } from "url";
import { dirname, join, resolve } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

const G = "\x1b[32m";
const Y = "\x1b[33m";
const C = "\x1b[36m";
const D = "\x1b[90m";
const R = "\x1b[0m";

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
  console.log(`  ${Y}!${R} Python 3 was not found.`);
  console.log(`  ${Y}!${R} Install Python 3.10+ and run: python3 -m pip install --user .`);
  process.exit(0);
}

const [pythonCommand, pythonPrefixArgs] = python;

// Step 1: Install Python package
console.log(`  Installing kyp-mem Python package...`);

const pipResult = run(
  pythonCommand,
  [...pythonPrefixArgs, "-m", "pip", "install", "--user", "."],
  { stdio: "inherit" },
);

if (pipResult.status !== 0) {
  console.log(`  ${Y}!${R} Could not auto-install the Python package.`);
  console.log(`  ${Y}!${R} Run manually from ${root}:`);
  console.log("    python3 -m pip install --user .");
  process.exit(0);
}

console.log(`  ${G}✓${R} Python package installed`);

// Step 2: Create default vault directory
const vaultDir = join(homedir(), ".kyp-mem", "vault");
try {
  mkdirSync(vaultDir, { recursive: true });
  console.log(`  ${G}✓${R} Vault ready at ${D}${vaultDir}${R}`);
} catch (_) {
  console.log(`  ${Y}!${R} Could not create vault at ${vaultDir}`);
}

// Step 3: Register MCP server with Claude Code (global)
const setupResult = run(
  pythonCommand,
  [...pythonPrefixArgs, "-m", "kyp_mem.cli", "setup-claude", "--global"],
  { stdio: "inherit" },
);

if (setupResult.status === 0) {
  console.log(`  ${G}✓${R} MCP server registered with Claude Code`);
} else {
  console.log(`  ${Y}!${R} Could not register MCP server — run manually: ${C}kyp-mem setup-claude --global${R}`);
}

// Step 4: Install hooks (global)
const hooksResult = run(
  pythonCommand,
  [...pythonPrefixArgs, "-m", "kyp_mem.cli", "install-hooks", "--global"],
  { stdio: "inherit" },
);

if (hooksResult.status === 0) {
  console.log(`  ${G}✓${R} Session capture hooks installed`);
} else {
  console.log(`  ${Y}!${R} Could not install hooks — run manually: ${C}kyp-mem install-hooks --global${R}`);
}

console.log();
console.log(`  ${C}KYP-MEM${R} is ready! Restart Claude Code to activate.`);
console.log(`  ${D}Vault: ${vaultDir}${R}`);
console.log(`  ${D}To customize vault path: kyp-mem init${R}`);
console.log();
