#!/usr/bin/env node

import { spawnSync } from "child_process";
import { mkdirSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { ensureVenv, findSystemPython, resolvePython, venvDir } from "./python-env.mjs";

const G = "\x1b[32m";
const Y = "\x1b[33m";
const C = "\x1b[36m";
const D = "\x1b[90m";
const R = "\x1b[0m";

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    stdio: options.stdio ?? "ignore",
    env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: "1" },
  });
}

if (process.env.KYP_MEM_SKIP_PYTHON_INSTALL === "1") {
  process.exit(0);
}

if (!findSystemPython()) {
  console.log(`  ${Y}!${R} Python 3 was not found.`);
  console.log(`  ${Y}!${R} Install Python 3.10+ and re-run: ${C}npm rebuild kyp-mem${R}`);
  process.exit(0);
}

// Step 1: Provision kyp-mem's own virtualenv with all dependencies.
// A dedicated venv works even when the system Python is externally managed
// (PEP 668), so users never have to create or manage one themselves.
console.log(`  Setting up kyp-mem Python environment...`);

if (!ensureVenv({ stdio: "inherit", force: true })) {
  console.log(`  ${Y}!${R} Could not provision the Python environment automatically.`);
  console.log(`  ${Y}!${R} kyp-mem will retry on first run, or run it now: ${C}kyp-mem doctor${R}`);
  process.exit(0);
}

console.log(`  ${G}✓${R} Python environment ready ${D}(${venvDir()})${R}`);

// Step 2: Create default vault directory
const vaultDir = join(homedir(), ".kyp-mem", "vault");
try {
  mkdirSync(vaultDir, { recursive: true });
  console.log(`  ${G}✓${R} Vault ready at ${D}${vaultDir}${R}`);
} catch (_) {
  console.log(`  ${Y}!${R} Could not create vault at ${vaultDir}`);
}

const [py, pre] = resolvePython({ allowBootstrap: false });

// Step 3: Register MCP server with Claude Code (global)
const setupResult = run(py, [...pre, "-m", "kyp_mem.cli", "setup-claude", "--global"], {
  stdio: "inherit",
});

if (setupResult.status === 0) {
  console.log(`  ${G}✓${R} MCP server registered with Claude Code`);
} else {
  console.log(`  ${Y}!${R} Could not register MCP server — run manually: ${C}kyp-mem setup-claude --global${R}`);
}

// Step 4: Install hooks (global)
const hooksResult = run(py, [...pre, "-m", "kyp_mem.cli", "install-hooks", "--global"], {
  stdio: "inherit",
});

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
