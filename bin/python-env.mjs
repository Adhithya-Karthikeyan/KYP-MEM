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

// Records the package version the venv was last provisioned for (so a
// kyp-mem upgrade transparently reinstalls dependencies on next run) and
// which tool created the venv's skeleton. The latter matters because a venv
// created by `uv venv` has no pip inside it at all — pip is simply never
// installed — so repair logic must know not to invoke `python -m pip` on one.
function stampFile() {
  return join(venvDir(), ".kyp-installed");
}

// Parses stamp file contents into { version, tool }. Pre-uv kyp-mem versions
// wrote a bare version string — always from a `python -m venv` build, since
// uv provisioning didn't exist yet — so a plain string parses as tool: "pip"
// instead of forcing every upgrader through a needless rebuild.
export function parseStamp(raw) {
  const text = (raw ?? "").trim();
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed.version === "string") {
      return { version: parsed.version, tool: parsed.tool === "uv" ? "uv" : "pip" };
    }
  } catch (_) {
    // Not JSON: legacy bare-version stamp.
  }
  return { version: text, tool: "pip" };
}

export function readStamp() {
  try {
    return parseStamp(readFileSync(stampFile(), "utf8"));
  } catch (_) {
    return null;
  }
}

function writeStamp(tool) {
  try {
    writeFileSync(stampFile(), JSON.stringify({ version: packageVersion(), tool }));
  } catch (_) {}
}

// The batteries-included install spec: pyproject.toml keeps the base install
// tiny and puts the web UI + semantic search behind optional extras. The npm
// path is meant to "just work" out of the box, so it opts into both by
// default (pip and uv both accept `/abs/path[extra1,extra2]`). Set
// KYP_MEM_LITE=1 to install only the tiny base, e.g. for constrained
// environments that will never use the UI or vector search.
export function installSpec() {
  return process.env.KYP_MEM_LITE === "1" ? projectRoot : `${projectRoot}[ui,vector]`;
}

function run(command, cmdArgs, stdio = "ignore", extraEnv = {}) {
  return spawnSync(command, cmdArgs, {
    stdio,
    env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: "1", ...extraEnv },
  });
}

// Venvs provisioned before this version carry the old ChromaDB dependency
// tree (~430 MB) that 1.2.0 stopped needing. pip and uv upgrades install new
// requirements but never remove no-longer-required packages, so upgrading in
// place would keep the old weight on disk forever. Rebuilding once from
// scratch is cheap (seconds with uv) and guarantees the slim footprint.
export const REBUILD_BELOW = "1.2.0";

export function versionLt(a, b) {
  const pa = String(a).split(".").map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) < (pb[i] ?? 0);
  }
  return false;
}

// Whether uv is on PATH. uv provisions a venv roughly 100x faster than
// `python -m venv`, installs packages roughly 60x faster than pip (warm
// cache), and can download a managed CPython itself when no suitable
// interpreter exists anywhere on the system — sidestepping findSystemPython()
// entirely. Prefer it whenever present.
export function probeUv() {
  return run("uv", ["--version"]).status === 0;
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
  const stamp = readStamp();
  return stamp?.version === packageVersion();
}

// Where uv stores any CPython it downloads on kyp-mem's behalf. Pointed at
// kyp-mem's own directory (rather than uv's shared default) so kyp-mem's
// entire footprint — venv and any interpreter it needed — stays removable as
// one unit, and so it never quietly reuses (or pollutes) a Python the user
// manages for other projects via uv.
function uvPythonInstallDir() {
  return join(homedir(), ".kyp-mem", "python");
}

// uv path: create the venv with `uv venv` (which auto-downloads a managed
// CPython if nothing suitable is already installed) and install with
// `uv pip install`. uv-built venvs never have pip inside them — uv doesn't
// need it — so `existingTool` is threaded through from the caller and only
// overwritten to "uv" when this call is the one that creates the venv;
// reusing an already-existing pip-built venv (e.g. just for a faster
// dependency reinstall on upgrade) must not relabel it as a uv venv, or a
// later uv disappearance would trigger an unnecessary rebuild of a venv that
// still has a perfectly good pip inside it.
function ensureVenvWithUv(stdio, existingTool) {
  const env = { UV_PYTHON_INSTALL_DIR: uvPythonInstallDir() };
  let tool = existingTool ?? "pip";

  if (!existsSync(venvPython())) {
    const created = run("uv", ["venv", "--python", ">=3.10", venvDir()], stdio, env);
    if (created.status !== 0 || !existsSync(venvPython())) return false;
    tool = "uv";
  }

  const installed = run(
    "uv",
    ["pip", "install", "--python", venvPython(), installSpec()],
    stdio,
    env
  );
  if (installed.status !== 0) return false;

  writeStamp(tool);
  return true;
}

// Fallback path when uv is not on PATH: today's plain python -m venv + pip
// flow, unchanged, so offline/no-uv environments keep working exactly as
// before.
function ensureVenvWithPip(stdio) {
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
  const installed = run(py, ["-m", "pip", "install", installSpec()], stdio);
  if (installed.status !== 0) return false;

  writeStamp("pip");
  return true;
}

// Create the venv (if needed) and install kyp-mem + its dependencies into it.
// Returns true on success. `stdio` controls output; pass "inherit" during
// `npm install` so users see progress, "ignore" for runtime bootstrap.
//
// Prefers uv when present (see ensureVenvWithUv for why); falls back to the
// plain python -m venv + pip flow otherwise.
export function ensureVenv({ stdio = "ignore", force = false } = {}) {
  if (!force && venvReady()) return true;

  // Read the stamp before any rebuild below might delete the venv (and the
  // stamp file living inside it) — we need to know how the existing venv, if
  // any, was built.
  const stamp = readStamp();
  const uv = probeUv();

  // A venv left behind by an older kyp-mem may have been built from a Python
  // that is now too old. Reusing it makes the install unrepairable — pip (or
  // uv) fails on requires-python every time — so replace it rather than
  // trying to install into it.
  if (existsSync(venvPython()) && !venvPythonSupported()) {
    rmSync(venvDir(), { recursive: true, force: true });
  } else if (existsSync(venvPython()) && stamp?.tool === "uv" && !uv) {
    // uv-built venvs contain no pip at all. If uv has since disappeared from
    // PATH there is no tool left inside the venv to repair it with — delete
    // and rebuild from scratch via whichever path is available now.
    rmSync(venvDir(), { recursive: true, force: true });
  } else if (existsSync(venvPython()) && stamp && versionLt(stamp.version, REBUILD_BELOW)) {
    // Crossing the 1.2.0 boundary: shed the old backend's dependency tree by
    // rebuilding, since an in-place upgrade would leave it installed.
    rmSync(venvDir(), { recursive: true, force: true });
  }

  return uv ? ensureVenvWithUv(stdio, stamp?.tool) : ensureVenvWithPip(stdio);
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
