#!/usr/bin/env node

import { execFileSync, execSync } from "child_process";

const args = process.argv.slice(2);

function tryRun(cmd, cmdArgs) {
  try {
    execFileSync(cmd, cmdArgs, { stdio: "inherit" });
    return true;
  } catch {
    return false;
  }
}

// Try the installed kyp-mem binary first
if (tryRun("kyp-mem", args)) process.exit(0);

// Fallback: python3 -m kyp_mem.cli
if (tryRun("python3", ["-m", "kyp_mem.cli", ...args])) process.exit(0);

// Fallback: python -m kyp_mem.cli
if (tryRun("python", ["-m", "kyp_mem.cli", ...args])) process.exit(0);

console.error("");
console.error("  \x1b[31mError:\x1b[0m kyp-mem Python package not found.");
console.error("");
console.error("  Install it:");
console.error("    \x1b[33mpip install kyp-mem\x1b[0m");
console.error("  Or from source:");
console.error("    \x1b[33mpip install git+https://github.com/Adhithya-Karthikeyan/KYP-MEM.git\x1b[0m");
console.error("");
process.exit(1);
