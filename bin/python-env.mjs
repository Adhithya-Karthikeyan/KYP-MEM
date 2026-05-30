// Shared Python environment management for kyp-mem.
//
// kyp-mem ships a Python backend, but modern system interpreters (Homebrew,
// recent Debian/Ubuntu) are "externally managed" (PEP 668), so installing
// dependencies into them with pip is blocked. Rather than ask every user to
// create and manage a virtualenv, kyp-mem owns one: it is created at install
// time, auto-detected at runtime, and lazily rebuilt if it ever goes missing.
// Users never have to create, activate, or even know about it.

import { spawnSync } from "child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { homedir } from "os";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
export const projectRoot = resolve(__dirname, "..");
const isWin = process.platform === "win32";

export function packageVersion() {
  try {
    const pkg = JSON.parse(readFileSync(join(projectRoot, "package.json"), "utf8"));
    return pkg.version || "0";
  } catch (_) {
    return "0";
  }
}

export function venvDir() {
  return join(homedir(), ".kyp-mem", "venv");
}

export function venvPython() {
  return isWin
    ? join(venvDir(), "Scripts", "python.exe")
    : join(venvDir(), "bin", "python");
}

// Records the package version the venv was last provisioned for, so a kyp-mem
// upgrade transparently reinstalls dependencies on next run.
function stampFile() {
  return join(venvDir(), ".kyp-installed");
}

function run(command, cmdArgs, stdio = "ignore") {
  return spawnSync(command, cmdArgs, {
    stdio,
    env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: "1" },
  });
}

// Find a system Python to build the venv from (or to fall back to).
export function findSystemPython() {
  const candidates = [];
  if (process.env.KYP_MEM_PYTHON) candidates.push([process.env.KYP_MEM_PYTHON, []]);
  if (isWin) candidates.push(["py", ["-3"]]);
  candidates.push(["python3", []], ["python", []]);

  for (const [command, prefixArgs] of candidates) {
    const result = run(command, [...prefixArgs, "--version"]);
    if (result.status === 0) return [command, prefixArgs];
  }
  return null;
}

// The venv exists and was provisioned for the current package version.
export function venvReady() {
  if (!existsSync(venvPython())) return false;
  try {
    return readFileSync(stampFile(), "utf8").trim() === packageVersion();
  } catch (_) {
    return false;
  }
}

// Create the venv (if needed) and install kyp-mem + its dependencies into it.
// Returns true on success. `stdio` controls pip/venv output; pass "inherit"
// during `npm install` so users see progress, "ignore" for runtime bootstrap.
export function ensureVenv({ stdio = "ignore", force = false } = {}) {
  if (!force && venvReady()) return true;

  const sys = findSystemPython();
  if (!sys) return false;
  const [cmd, pre] = sys;

  if (!existsSync(venvPython())) {
    mkdirSync(venvDir(), { recursive: true });
    const created = run(cmd, [...pre, "-m", "venv", venvDir()], stdio);
    if (created.status !== 0 || !existsSync(venvPython())) return false;
  }

  const py = venvPython();
  run(py, ["-m", "pip", "install", "--upgrade", "pip"], stdio);
  const installed = run(py, ["-m", "pip", "install", projectRoot], stdio);
  if (installed.status !== 0) return false;

  try {
    writeFileSync(stampFile(), packageVersion());
  } catch (_) {}
  return true;
}

// Resolve the Python interpreter kyp-mem should run with.
//   1. KYP_MEM_PYTHON, if set, is an explicit override (power-user escape hatch).
//   2. The managed venv — bootstrapped on demand when `allowBootstrap`.
//   3. Whatever venv/system Python exists, even if not fully provisioned.
// `allowBootstrap: false` skips the (slow) install step for latency-sensitive
// callers so they never block on a pip run.
export function resolvePython({ allowBootstrap = true } = {}) {
  if (process.env.KYP_MEM_PYTHON) return [process.env.KYP_MEM_PYTHON, []];
  if (venvReady()) return [venvPython(), []];
  if (allowBootstrap && ensureVenv()) return [venvPython(), []];
  if (existsSync(venvPython())) return [venvPython(), []];
  return findSystemPython();
}
