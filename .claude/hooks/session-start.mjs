// SessionStart (startup|resume|clear|compact): load project status and current milestone into context.
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { readInput, STATE_DIR } from './lib/common.mjs';
import { buildStatus } from './lib/status.mjs';

const input = readInput();
let text = buildStatus();

if (input.source === 'compact') {
  const snap = path.join(STATE_DIR, 'last-compact.md');
  if (existsSync(snap)) {
    text += `\n\n## Snapshot saved just before compaction\n${readFileSync(snap, 'utf8').slice(0, 6000)}`;
  }
}

process.stdout.write(`${text}\n`);
