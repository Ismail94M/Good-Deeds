// Shared helpers for the Good Deeds Claude Code hooks.
// Hooks never read or print .env contents.
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Repo root = two levels up from .claude/hooks/lib (works whatever folder the session started in).
export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
export const STATE_DIR = path.join(ROOT, '.claude', 'state');
export const IS_WIN = process.platform === 'win32';

export function readInput() {
  try {
    const raw = readFileSync(0, 'utf8');
    return raw.trim() ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function ensureStateDir() {
  if (!existsSync(STATE_DIR)) mkdirSync(STATE_DIR, { recursive: true });
}

/** Repo-relative path with forward slashes, or null if outside the repo. */
export function relToRoot(p) {
  if (!p) return null;
  const rel = path.relative(ROOT, path.resolve(p)).split(path.sep).join('/');
  if (rel.startsWith('..') || path.isAbsolute(rel)) return null;
  return rel;
}

/** Run a program without a shell. Returns {code, out}. */
export function run(cmd, args, { cwd = ROOT, timeoutMs = 120000 } = {}) {
  const r = spawnSync(cmd, args, { cwd, encoding: 'utf8', timeout: timeoutMs, maxBuffer: 32 * 1024 * 1024, windowsHide: true });
  if (r.error) return { code: -1, out: String(r.error.message) };
  return { code: r.status ?? -1, out: `${r.stdout ?? ''}${r.stderr ?? ''}` };
}

/** Run a node_modules/.bin tool from frontend/ (needs a shell on Windows for .cmd shims). */
export function runFrontendBin(bin, args, { timeoutMs = 300000 } = {}) {
  const cwd = path.join(ROOT, 'frontend');
  const exe = path.join('node_modules', '.bin', bin);
  const quoted = [exe, ...args].map((a) => (/[\s"&|<>^]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a)).join(' ');
  const r = spawnSync(quoted, { cwd, shell: true, encoding: 'utf8', timeout: timeoutMs, maxBuffer: 32 * 1024 * 1024, windowsHide: true });
  if (r.error) return { code: -1, out: String(r.error.message) };
  return { code: r.status ?? -1, out: `${r.stdout ?? ''}${r.stderr ?? ''}` };
}

/** Path to a backend venv tool (ruff/mypy/pytest), or null if the venv is missing. */
export function backendTool(name) {
  const candidates = [
    path.join(ROOT, 'backend', '.venv', 'Scripts', `${name}.exe`),
    path.join(ROOT, 'backend', '.venv', 'bin', name),
  ];
  return candidates.find((c) => existsSync(c)) ?? null;
}

export function runBackend(name, args, opts = {}) {
  const tool = backendTool(name);
  const cwd = path.join(ROOT, 'backend');
  if (tool) return run(tool, args, { cwd, ...opts });
  return run('uv', ['run', name, ...args], { cwd, ...opts }); // fallback, as in CLAUDE.md
}

export function git(args) {
  return run('git', args, { timeoutMs: 15000 });
}

export function tail(text, lines = 40) {
  const arr = String(text).trimEnd().split(/\r?\n/);
  return arr.slice(-lines).join('\n');
}
