#!/usr/bin/env node

import { execSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

function tryInstall(cmd) {
  try {
    execSync(cmd, { cwd: root, stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

// Check if already installed
try {
  execSync("kyp-mem --help", { stdio: "pipe" });
  process.exit(0);
} catch {}

console.log("  Installing kyp-mem Python package...");

if (tryInstall("pip install --user .")) {
  console.log("  \x1b[32m✓\x1b[0m kyp-mem installed successfully");
} else if (tryInstall("pip3 install --user .")) {
  console.log("  \x1b[32m✓\x1b[0m kyp-mem installed successfully");
} else if (tryInstall("python3 -m pip install --user .")) {
  console.log("  \x1b[32m✓\x1b[0m kyp-mem installed successfully");
} else {
  console.log("  \x1b[33m!\x1b[0m Could not auto-install Python package.");
  console.log("  \x1b[33m!\x1b[0m Run manually: pip install kyp-mem");
}
