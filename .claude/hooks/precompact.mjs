// PreCompact (manual|auto): stdout becomes extra instructions for the compaction summary.
// Also saves a snapshot that SessionStart(compact) reloads afterwards.
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { readInput, ensureStateDir, STATE_DIR } from './lib/common.mjs';
import { buildStatus } from './lib/status.mjs';

const input = readInput();
const status = buildStatus({ includeDocker: false });

ensureStateDir();
writeFileSync(
  path.join(STATE_DIR, 'last-compact.md'),
  `Saved ${new Date().toISOString()} (trigger: ${input.trigger ?? 'unknown'})\n\n${status}\n`,
  'utf8',
);

const instructions = `When writing the compaction summary for the Good Deeds project, you MUST preserve:
1. Every standing rule listed below, verbatim.
2. The current milestone and its stage (design / PO questions / build / QA / security review / PO review).
3. Every product-owner decision taken in this conversation (question number, chosen option, date) and where it was recorded (PRD, ROADMAP, CLAUDE.md, docs/M*-DESIGN.md).
4. The open PO questions and which one was last asked and is awaiting an answer, with the exact option labels offered.
5. Pending tasks and their order, including anything the PO approved but that is not yet done (commits, pushes awaiting approval, agent tasks in flight).
6. Uncommitted files and which branch they belong on.
7. Known errors and their fixes from this session that would otherwise be repeated.
Do not include secrets, .env values, OTP codes or the user's personal details.

${status}`;

process.stdout.write(instructions.slice(0, 9500));
