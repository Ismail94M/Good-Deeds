// PreToolUse guard: protects secrets, git history and the database.
// Only ever answers "deny" or "ask" (never "allow"); silence = normal permission flow.
import { existsSync } from 'node:fs';
import path from 'node:path';
import { readInput, relToRoot, git, ROOT } from './lib/common.mjs';

const input = readInput();
const tool = input.tool_name ?? '';
const ti = input.tool_input ?? {};

function decide(decision, reason) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: decision, permissionDecisionReason: `[guard] ${reason}` },
    }),
  );
  process.exit(0);
}

// ---------- secret files ----------
function isSecretPath(p) {
  if (!p) return false;
  const base = String(p).replace(/\\/g, '/').split('/').pop().toLowerCase();
  if (/^\.env(\..+)?$/.test(base) && base !== '.env.example') return true;
  if (/\.(pem|key|p12|pfx)$/.test(base)) return true;
  if (/^id_(rsa|ed25519|ecdsa|dsa)$/.test(base)) return true;
  if (/(service[-_]?account|credentials?|gcs[-_]?key).*\.json$/.test(base)) return true;
  return false;
}

// ---------- file tools ----------
if (['Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'].includes(tool)) {
  const fp = ti.file_path ?? ti.notebook_path;
  if (isSecretPath(fp)) decide('deny', `Secret file (${path.basename(fp)}) is off limits. Use .env.example for variable names.`);

  if (tool !== 'Read') {
    const rel = relToRoot(fp) ?? '';
    const exists = fp && existsSync(fp);
    if (/^backend\/alembic\/versions\/.+\.py$/.test(rel) && exists)
      decide('ask', `Editing an existing migration (${path.basename(rel)}). Applied migrations are append-only; add a new migration instead unless this one is unreleased.`);
    if (/(^|\/)(uv\.lock|package-lock\.json)$/.test(rel))
      decide('ask', `Hand-editing a lockfile (${rel}). Lockfiles should be changed by uv/npm.`);
    if (/^\.github\/workflows\//.test(rel)) decide('ask', `Changing CI workflow ${rel}.`);
    if (/^\.claude\/(settings\.json|hooks\/)/.test(rel)) decide('ask', `Changing Claude Code hook configuration (${rel}).`);
  }
  process.exit(0);
}

if (tool === 'Grep') {
  if (isSecretPath(ti.path) || /\.env(?!\.example)/i.test(ti.glob ?? ''))
    decide('deny', 'Searching inside .env/secret files is not allowed.');
  process.exit(0);
}

// ---------- shell ----------
if (tool !== 'Bash' && tool !== 'PowerShell') process.exit(0);
const cmd = String(ti.command ?? '');
const c = cmd.replace(/\s+/g, ' ');

// Secrets: any reference to a .env file (except .env.example), or env dumps.
const ENV_FILE = /(?:^|[\s'"=:;|&<>()\/\\`])\.env(?:\.(?!example\b)[\w-]+)?(?=$|[\s'";|&<>)`])/;
if (ENV_FILE.test(cmd)) decide('deny', 'Commands that touch .env files are blocked so secrets are never read or printed. Use .env.example for variable names.');
if (/\bprintenv\b/.test(c) || /(^|[;&|(]\s*)(env|export -p|declare -x|set)\s*($|[;&|>)])/.test(c))
  decide('deny', 'Dumping environment variables can print secrets.');
if (/\b(gci|ls|dir|Get-ChildItem)\s+env:/i.test(c) || /^\s*\$env:\s*$/i.test(c))
  decide('deny', 'Dumping environment variables can print secrets.');
if (/\bdocker(-compose|\s+compose)\b.*\bconfig\b/.test(c) && !/--(services|volumes|quiet)|\s-q\b/.test(c))
  decide('deny', '`docker compose config` prints resolved secrets. Use `--services` or `--quiet`.');
if (/\bdocker\b.*\bexec\b.*\b(env|printenv)\b/.test(c)) decide('deny', 'Printing a container environment can expose secrets.');
if (/\bdocker\s+(container\s+)?inspect\b/.test(c) && !/--format|-f\s/.test(c))
  decide('deny', '`docker inspect` prints container env (secrets). Use --format for specific fields.');

// Git.
if (/\bgit\s+commit\b/.test(c) && /\s(--no-verify|-n)\b/.test(c)) decide('deny', 'Skipping git hooks (--no-verify) is not allowed.');
if (/\bgit\s+push\b/.test(c)) {
  const force = /\s(--force(-with-lease)?|-f)\b|\s\+[\w/-]+/.test(c);
  if (force && /\b(main|master)\b/.test(c)) decide('deny', 'Force-pushing to main is not allowed.');
  decide('ask', 'Push to GitHub needs product-owner approval (standing rule: never push without approval).');
}
if (/\bgit\s+commit\b/.test(c)) {
  const b = git(['rev-parse', '--abbrev-ref', 'HEAD']).out.trim();
  if (b === 'main' || b === 'master') decide('ask', `Committing directly on ${b}. Each milestone should be on its own feature branch.`);
}
if (/\bgit\s+reset\b.*--hard/.test(c)) decide('ask', '`git reset --hard` discards uncommitted work.');
if (/\bgit\s+clean\b.*\s-[a-zA-Z]*f/.test(c)) decide('ask', '`git clean -f` deletes untracked files.');
if (/\bgit\s+checkout\b.*\s--\s/.test(c) || /\bgit\s+checkout\s+\.(\s|$)/.test(c)) decide('ask', 'This git checkout discards local changes.');
if (/\bgit\s+restore\b/.test(c) && !/--staged/.test(c)) decide('ask', '`git restore` discards local changes.');
if (/\bgit\s+branch\b.*\s-D\b/.test(c)) decide('ask', 'Force-deleting a branch.');
if (/\bgit\s+stash\s+(drop|clear)\b/.test(c)) decide('ask', 'Dropping stashed work.');

// Deletes.
const rmMatch = /\brm\s+((?:-[a-zA-Z]+\s+|--[a-z-]+\s+)*)(.*)$/.exec(c);
if (rmMatch) {
  const flags = rmMatch[1];
  const recursive = /-[a-zA-Z]*[rR]|--recursive/.test(flags);
  const force = /-[a-zA-Z]*f|--force/.test(flags);
  if (recursive && force) {
    const targets = rmMatch[2].split(/[;&|]/)[0].trim().split(/\s+/);
    const rootish = targets.some((t) => {
      const u = t.replace(/^["']|["']$/g, '').replace(/\\/g, '/').replace(/\/+$/, '');
      return ['', '/', '~', '.', '..', '*', '$HOME', '/c', 'C:', 'c:'].includes(u) || path.resolve(u) === ROOT;
    });
    if (rootish) decide('deny', 'rm -rf on /, ~, .. or the project root is blocked.');
    decide('ask', 'Recursive force delete.');
  }
}
if (/\bRemove-Item\b.*-Recurse/i.test(c)) decide('ask', 'Recursive delete (Remove-Item -Recurse).');

// Database protection.
if (/\bdocker(-compose|\s+compose)\b.*\bdown\b.*(\s-v\b|--volumes)/.test(c)) decide('ask', 'This deletes the Postgres volume (all local data).');
if (/\bdocker\s+(volume\s+(rm|prune)|system\s+prune)\b/.test(c)) decide('ask', 'This can delete the database volume.');
if (/\balembic\b.*\b(downgrade|stamp)\b/.test(c)) decide('ask', 'Alembic downgrade/stamp changes the database schema history.');
if (/\bdropdb\b/.test(c)) decide('ask', 'dropdb deletes a database.');
const firstWord = c.trim().split(' ')[0];
if (!['grep', 'rg', 'git', 'echo'].includes(firstWord)) {
  const destructiveSql =
    /\b(DROP\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|EXTENSION|TYPE|ROLE|COLUMN)|TRUNCATE\b|DELETE\s+FROM|UPDATE\s+[\w."]+\s+SET|ALTER\s+TABLE\s+\S+\s+DROP)/i;
  if (destructiveSql.test(c)) decide('ask', 'Destructive SQL (DROP/TRUNCATE/DELETE/UPDATE/ALTER … DROP) against a database.');
}

process.exit(0);
