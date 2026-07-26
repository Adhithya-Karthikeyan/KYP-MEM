// Shared Python environment management for kyp-mem.
//
// kyp-mem ships a Python backend, but modern system interpreters (Homebrew,
// recent Debian/Ubuntu) are "externally managed" (PEP 668), so installing
// dependencies into them with pip is blocked. Rather than ask every user to
// create and manage a virtualenv, kyp-mem owns one: it is created at install
// time, auto-detected at runtime, and lazily rebuilt if it ever goes missing.
// Users never have to create, activate, or even know about it.

import { spawnSync } from "child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "fs";
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

// Minimum interpreter, kept in step with requires-python in pyproject.toml.
export const MIN_PYTHON = [3, 10];
const SUPPORTED_SERIES = ["3.14", "3.13", "3.12", "3.11", "3.10"];

// Ask the interpreter whether it qualifies, rather than parsing version strings
// (which vary by distro and would need locale-safe stdout capture).
function satisfiesMinimum(command, prefixArgs) {
  const check = `import sys; sys.exit(0 if sys.version_info >= (${MIN_PYTHON[0]}, ${MIN_PYTHON[1]}) else 1)`;
  return run(command, [...prefixArgs, "-c", check]).status === 0;
}

// True when KYP_MEM_PYTHON is set but points at an interpreter that is too old,
// so callers can say that instead of "Python not found".
export function overrideIsTooOld() {
  const override = process.env.KYP_MEM_PYTHON;
  if (!override) return false;
  return !satisfiesMinimum(override, []);
}

// Find a system Python new enough to run kyp-mem.
//
// Previously this accepted whatever answered `--version` first, with no version
// check at all. On a stock macOS box /usr/bin/python3 is 3.9, Debian 11 ships
// 3.9 and Ubuntu 20.04 ships 3.8 — so the venv got built from an interpreter
// the package cannot install into, pip failed, and the install still exited 0.
// Versioned names are tried first, newest first, because a supported
// interpreter is usually present alongside an unsupported bare `python3`.
export function findSystemPython() {
  const candidates = [];
  if (process.env.KYP_MEM_PYTHON) candidates.push([process.env.KYP_MEM_PYTHON, []]);
  for (const series of SUPPORTED_SERIES) {
    if (isWin) candidates.push(["py", [`-${series}`]]);
    candidates.push([`python${series}`, []]);
  }
  if (isWin) candidates.push(["py", ["-3"]]);
  candidates.push(["python3", []], ["python", []]);

  for (const [command, prefixArgs] of candidates) {
    if (satisfiesMinimum(command, prefixArgs)) return [command, prefixArgs];
  }
  return null;
}

// Whether the managed venv's interpreter is still new enough. A venv built by
// an older kyp-mem from a 3.9 interpreter must be rebuilt, not reused.
export function venvPythonSupported() {
  return existsSync(venvPython()) && satisfiesMinimum(venvPython(), []);
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

  // A venv left behind by an older kyp-mem may have been built from a Python
  // that is now too old. Reusing it makes the install unrepairable — pip fails
  // on requires-python every time, and no amount of reinstalling helps — so
  // replace it rather than trying to install into it.
  if (existsSync(venvPython()) && !venvPythonSupported()) {
    rmSync(venvDir(), { recursive: true, force: true });
  }

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
  // Only fall back to an unprovisioned venv if its interpreter is actually
  // usable. Returning a venv built from a too-old Python turns every later
  // command into a raw traceback instead of a clear setup message.
  if (venvPythonSupported()) return [venvPython(), []];
  return findSystemPython();
}
