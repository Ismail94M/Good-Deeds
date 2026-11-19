// Stop: validate changed code before Claude finishes a turn.
// - Skips instantly if no code changed, if the same changes already passed, or if background agents are running.
// - Backend: ruff, ruff format --check, mypy, pytest on RELATED tests only (never the full suite).
// - Frontend: oxlint, prettier --check, tsc -b, vitest related, vite build.
// - On failure: blocks once with the error tail; if Claude is already continuing from this hook, it only reports.
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { readInput, ROOT, STATE_DIR, ensureStateDir, git, runBackend, runFrontendBin, tail } from './lib/common.mjs';

const input = readInput();
if (Array.isArray(input.background_tasks) && input.background_tasks.length > 0) process.exit(0);

// ---- which code files changed (uncommitted, incl. untracked) ----
const BACKEND = /^backend\/((app|tests|alembic)\/.+\.py|pyproject\.toml|uv\.lock)$/;
const FRONTEND = /^frontend\/(src\/.+|package\.json|package-lock\.json|vite\.config\.ts|tsconfig[^/]*\.json|index\.html)$/;

const porcelain = git(['status', '--porcelain', '-z', '--untracked-files=all']).out;
const entries = porcelain.split('\0').filter(Boolean);
const changed = [];
for (let i = 0; i < entries.length; i++) {
  const e = entries[i];
  const code = e.slice(0, 2);
  const p = e.slice(3);
  if (code.startsWith('R') || code.startsWith('C')) i++; // skip the "from" path of renames
  if (BACKEND.test(p) || FRONTEND.test(p)) changed.push(p);
}
if (changed.length === 0) process.exit(0);

// ---- fingerprint: skip if exactly these changes already passed ----
const h = createHash('sha256');
for (const p of [...changed].sort()) {
  const abs = path.join(ROOT, p);
  h.update(p);
  h.update(existsSync(abs) ? createHash('sha256').update(readFileSync(abs)).digest('hex') : 'deleted');
}
const fingerprint = h.digest('hex');
const passFile = path.join(STATE_DIR, 'validate-pass.json');
try {
  if (JSON.parse(readFileSync(passFile, 'utf8')).fingerprint === fingerprint) process.exit(0);
} catch {}

const backendFiles = changed.filter((p) => BACKEND.test(p));
const frontendFiles = changed.filter((p) => FRONTEND.test(p));
const failures = [];
const warnings = [];
const ran = [];

function check(label, res) {
  ran.push(label);
  if (res.code !== 0) failures.push(`### ${label} (exit ${res.code})\n${tail(res.out, 40)}`);
  return res.code === 0;
}

function dbUp() {
  return new Promise((resolve) => {
    const s = net.connect({ host: '127.0.0.1', port: 5432 });
    const done = (ok) => { s.destroy(); resolve(ok); };
    s.setTimeout(1500, () => done(false));
    s.once('connect', () => done(true));
    s.once('error', () => done(false));
  });
}

// ---- backend ----
if (backendFiles.length) {
  check('ruff check', runBackend('ruff', ['check', '.', '--quiet'], { timeoutMs: 60000 }));
  check('ruff format --check', runBackend('ruff', ['format', '--check', '--quiet', '.'], { timeoutMs: 60000 }));
  check('mypy app tests', runBackend('mypy', ['app', 'tests'], { timeoutMs: 300000 }));

  // Related tests only.
  const targets = new Set();
  for (const p of backendFiles) {
    const rel = p.replace(/^backend\//, '');
    if (!existsSync(path.join(ROOT, p))) continue;
    if (/^tests\/.*test_[^/]+\.py$/.test(rel)) targets.add(rel);
    else if (/^tests\/(.+\/)?(conftest|helpers|[^/]*_helpers)\.py$/.test(rel)) targets.add(path.posix.dirname(rel));
    const mod = /^app\/modules\/([^/]+)\//.exec(rel);
    if (mod && existsSync(path.join(ROOT, 'backend', 'tests', mod[1]))) targets.add(`tests/${mod[1]}`);
    if (/^(app\/(core\/|main\.py|modules\/registry\.py)|alembic\/)/.test(rel) || rel === 'pyproject.toml') {
      targets.add('tests/test_architecture.py');
      for (const f of ['test_audit.py', 'test_errors.py', 'test_events.py', 'test_health.py', 'test_runtime_config.py', 'test_log_redaction.py'])
        if (existsSync(path.join(ROOT, 'backend', 'tests', f))) targets.add(`tests/${f}`);
    }
    if (/^app\//.test(rel)) targets.add('tests/test_architecture.py');
  }
  // Drop files already covered by a selected directory.
  const dirs = [...targets].filter((t) => !t.endsWith('.py'));
  const list = [...targets].filter((t) => !dirs.some((d) => t !== d && t.startsWith(`${d}/`)));
  if (list.length) {
    if (await dbUp()) {
      check(`pytest ${list.join(' ')}`, runBackend('pytest', ['-q', '-x', '--no-header', '-p', 'no:cacheprovider', ...list], { timeoutMs: 780000 }));
    } else {
      warnings.push(`Postgres is not reachable on localhost:5432, so related backend tests were NOT run (${list.join(' ')}). Start it with \`docker compose up -d db\`.`);
    }
  }
}

// ---- frontend ----
if (frontendFiles.length) {
  check('oxlint', runFrontendBin('oxlint', [], { timeoutMs: 120000 }));
  check('prettier --check src', runFrontendBin('prettier', ['--check', '--log-level', 'warn', 'src'], { timeoutMs: 120000 }));
  const typesOk = check('tsc -b', runFrontendBin('tsc', ['-b'], { timeoutMs: 300000 }));
  const srcFiles = frontendFiles.filter((p) => p.startsWith('frontend/src/') && existsSync(path.join(ROOT, p))).map((p) => p.replace(/^frontend\//, ''));
  if (srcFiles.length) check('vitest related', runFrontendBin('vitest', ['related', '--run', '--passWithNoTests', ...srcFiles], { timeoutMs: 300000 }));
  if (typesOk) check('vite build', runFrontendBin('vite', ['build', '--logLevel', 'warn'], { timeoutMs: 300000 }));
  else warnings.push('vite build skipped because tsc failed.');
}

ensureStateDir();

if (failures.length === 0) {
  writeFileSync(passFile, JSON.stringify({ fingerprint, at: new Date().toISOString(), ran }, null, 2), 'utf8');
  const msg = `Stop checks passed: ${ran.join(', ')}.${warnings.length ? ` Warning: ${warnings.join(' ')}` : ''}`;
  process.stdout.write(JSON.stringify({ systemMessage: msg }));
  process.exit(0);
}

const report = `Validation failed for the changed code (${changed.length} file(s)). Fix these before finishing:\n\n${failures.join('\n\n')}${warnings.length ? `\n\nNotes: ${warnings.join(' ')}` : ''}`.slice(0, 9000);

if (input.stop_hook_active) {
  // Already continued once because of this hook: report only, never loop.
  process.stdout.write(JSON.stringify({ systemMessage: `Stop checks still failing (not blocking again): ${failures.map((f) => f.split('\n')[0].replace('### ', '')).join('; ')}` }));
  process.exit(0);
}
process.stdout.write(JSON.stringify({ decision: 'block', reason: report }));
process.exit(0);
