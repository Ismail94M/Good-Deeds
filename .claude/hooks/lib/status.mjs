// Builds the project status block used by SessionStart and PreCompact.
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { ROOT, git, run } from './common.mjs';

export const STANDING_RULES = [
  'Never push to GitHub without the product owner\'s approval.',
  'Each milestone is committed on its own feature branch; do not start the next milestone without the PO\'s go-ahead.',
  'Before any business-logic change, ask the PO step by step, one topic at a time, with concrete examples (Ahmed/Priya/Rahul).',
  'Python file writes always use encoding="utf-8" (cp1252 once wiped ROADMAP.md).',
  'Never read or print .env contents or other secrets.',
  'Commit messages end with the Co-Authored-By line from the session attribution.',
];

function read(rel) {
  const p = path.join(ROOT, rel);
  return existsSync(p) ? readFileSync(p, 'utf8') : '';
}

export function currentMilestone() {
  const lines = read('docs/ROADMAP.md').split(/\r?\n/);
  let lastDone = null;
  for (const l of lines) {
    const m = /^\*\*(M\d+)\s+([^*]+)\*\*(.*)$/.exec(l.trim());
    if (!m) continue;
    if (m[3].includes('✅')) lastDone = `${m[1]} ${m[2].trim()}`;
    else return { id: m[1], name: m[2].trim(), lastDone };
  }
  return { id: null, name: null, lastDone };
}

/** Open PO questions in docs/<M>-DESIGN.md: table rows "| Qnn |" without a "PO:" answer. */
export function openQuestions(milestoneId) {
  if (!milestoneId) return null;
  const doc = `docs/${milestoneId}-DESIGN.md`;
  const text = read(doc);
  if (!text) return null;
  const rows = text.split(/\r?\n/).filter((l) => /^\|\s*Q\d+\s*\|/.test(l));
  const open = rows.filter((r) => !/\*\*PO:/.test(r)).map((r) => /Q\d+/.exec(r)[0]);
  const decided = rows.length - open.length;
  return { doc, open, decided };
}

export function gitSummary() {
  const branch = git(['rev-parse', '--abbrev-ref', 'HEAD']).out.trim();
  const ab = git(['rev-list', '--left-right', '--count', '@{u}...HEAD']);
  let sync = 'no upstream';
  if (ab.code === 0) {
    const [behind, ahead] = ab.out.trim().split(/\s+/).map(Number);
    sync = `${ahead} ahead / ${behind} behind upstream`;
  }
  const st = git(['status', '--porcelain', '--untracked-files=normal']).out.split(/\r?\n/).filter(Boolean);
  const areas = {};
  for (const l of st) {
    const p = l.slice(3).replace(/^"|"$/g, '');
    const area = p.split('/')[0];
    (areas[area] ??= []).push(`${l.slice(0, 2).trim() || '?'} ${p}`);
  }
  const log = git(['log', '-3', '--oneline']).out.trim();
  return { branch, sync, areas, count: st.length, log };
}

export function dockerSummary() {
  const r = run('docker', ['compose', 'ps', '--format', '{{.Service}}={{.State}}'], { timeoutMs: 8000 });
  if (r.code !== 0) return 'Docker not running (start with `docker compose up -d`).';
  const s = r.out.trim().split(/\r?\n/).filter(Boolean).join(', ');
  return s || 'no services running';
}

export function buildStatus({ includeDocker = true } = {}) {
  const ms = currentMilestone();
  const q = openQuestions(ms.id);
  const g = gitSummary();
  const out = [];
  out.push('## Good Deeds project status (from hooks)');
  out.push(`- Current milestone: ${ms.id ? `${ms.id} ${ms.name}` : 'unknown'} (last done: ${ms.lastDone ?? 'none'})`);
  if (q) out.push(`- ${q.doc}: ${q.decided} PO questions decided, ${q.open.length} open${q.open.length ? ` (next: ${q.open[0]}; open: ${q.open.join(', ')})` : ''}`);
  out.push(`- Git: branch ${g.branch}, ${g.sync}, ${g.count} uncommitted path(s)`);
  for (const [area, items] of Object.entries(g.areas)) {
    out.push(`  - ${area}: ${items.slice(0, 8).join('; ')}${items.length > 8 ? `; …+${items.length - 8}` : ''}`);
  }
  out.push(`- Last commits:\n${g.log.split('\n').map((l) => `  - ${l}`).join('\n')}`);
  if (includeDocker) out.push(`- Docker: ${dockerSummary()}`);
  out.push('- Standing rules:');
  for (const r of STANDING_RULES) out.push(`  - ${r}`);
  return out.join('\n');
}
