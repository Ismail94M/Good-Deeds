// PostToolUse (Edit|Write): format + lint ONLY the edited file. No tests, no type-check.
// Remaining lint errors are sent back to Claude (exit 2).
import { existsSync } from 'node:fs';
import path from 'node:path';
import { readInput, relToRoot, runBackend, runFrontendBin, ROOT, tail } from './lib/common.mjs';

const input = readInput();
const fp = input.tool_input?.file_path;
const rel = relToRoot(fp);
if (!rel || !existsSync(fp)) process.exit(0);

const problems = [];

if (/^backend\/(?!\.venv\/).+\.py$/.test(rel)) {
  const file = path.relative(path.join(ROOT, 'backend'), fp);
  runBackend('ruff', ['format', '--quiet', file], { timeoutMs: 30000 });
  const chk = runBackend('ruff', ['check', '--fix', '--quiet', file], { timeoutMs: 30000 });
  if (chk.code !== 0) problems.push(`ruff check ${rel}:\n${tail(chk.out, 30)}`);
} else if (/^frontend\/src\/.+\.(tsx?|jsx?|css|json)$/.test(rel)) {
  const file = path.relative(path.join(ROOT, 'frontend'), fp);
  const fmt = runFrontendBin('prettier', ['--write', '--log-level', 'warn', file], { timeoutMs: 30000 });
  if (fmt.code !== 0) problems.push(`prettier ${rel}:\n${tail(fmt.out, 20)}`);
  if (/\.(tsx?|jsx?)$/.test(rel)) {
    const lint = runFrontendBin('oxlint', [file], { timeoutMs: 30000 });
    if (lint.code !== 0) problems.push(`oxlint ${rel}:\n${tail(lint.out, 30)}`);
  }
} else {
  process.exit(0);
}

if (problems.length) {
  process.stderr.write(`Formatted ${rel}, but lint problems remain. Please fix:\n\n${problems.join('\n\n')}\n`);
  process.exit(2);
}
process.exit(0);
