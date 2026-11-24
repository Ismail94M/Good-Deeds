/**
 * How each badge looks (PO 2026-11-24: premium achievement badges, UI only). The API sends only
 * `{slug, label}` per badge, so the ladder, level and icon of each known slug live here; any
 * other slug from config gets a plain "other" medal. Explanations come from `/scoring/options`
 * when it has loaded (signed in) and otherwise from the same wording here.
 */
import type { Badge } from './scoringApi'

export type BadgeFamily = 'recognised' | 'witness' | 'other'
/** 0 starter, 1 bronze, 2 silver, 3 gold. */
export type BadgeLevel = 0 | 1 | 2 | 3

type Known = {
  family: BadgeFamily
  level: BadgeLevel
  step: number
  mark?: string
  description: string
}

const KNOWN: Record<string, Known> = {
  first_recognised: {
    family: 'recognised',
    level: 0,
    step: 1,
    description: 'Recognised for a verified deed',
  },
  kind_5: {
    family: 'recognised',
    level: 1,
    step: 2,
    mark: '5',
    description: 'Recognised for 5 verified deeds',
  },
  kind_25: {
    family: 'recognised',
    level: 2,
    step: 3,
    mark: '25',
    description: 'Recognised for 25 verified deeds',
  },
  kind_100: {
    family: 'recognised',
    level: 3,
    step: 4,
    mark: '100',
    description: 'Recognised for 100 verified deeds',
  },
  first_witness: {
    family: 'witness',
    level: 0,
    step: 1,
    description: 'Recognised a deed you witnessed',
  },
  witness_10: {
    family: 'witness',
    level: 1,
    step: 2,
    mark: '10',
    description: 'Recognised 10 deeds you witnessed',
  },
  witness_50: {
    family: 'witness',
    level: 2,
    step: 3,
    mark: '50',
    description: 'Recognised 50 deeds you witnessed',
  },
}

const STEPS: Record<BadgeFamily, number> = { recognised: 4, witness: 3, other: 1 }

export const FAMILY: Record<
  BadgeFamily,
  { name: string; face: string; glow: string; ink: string }
> = {
  recognised: {
    name: 'Kindness',
    face: 'linear-gradient(140deg, #fb7185 0%, #f43f5e 45%, #f97316 100%)',
    glow: 'rgba(244, 63, 94, 0.35)',
    ink: '#be123c',
  },
  witness: {
    name: 'Witness',
    face: 'linear-gradient(140deg, #38bdf8 0%, #3b82f6 50%, #6366f1 100%)',
    glow: 'rgba(79, 70, 229, 0.35)',
    ink: '#4338ca',
  },
  other: {
    name: 'Special',
    face: 'linear-gradient(140deg, #a78bfa 0%, #8b5cf6 50%, #d946ef 100%)',
    glow: 'rgba(139, 92, 246, 0.35)',
    ink: '#6d28d9',
  },
}

export const LEVEL: Record<BadgeLevel, { name: string; rim: string }> = {
  0: { name: 'Starter', rim: 'linear-gradient(140deg, #ffffff, #e2e8f0 60%, #f8fafc)' },
  1: { name: 'Bronze', rim: 'linear-gradient(140deg, #f6d0a8, #a6662f 55%, #f0b787)' },
  2: { name: 'Silver', rim: 'linear-gradient(140deg, #ffffff, #94a3b8 55%, #e2e8f0)' },
  3: { name: 'Gold', rim: 'linear-gradient(140deg, #fff7d1, #d97706 50%, #fde68a)' },
}

export type BadgeInfo = Badge & {
  family: BadgeFamily
  level: BadgeLevel
  step: number
  steps: number
  mark?: string
  description: string
}

export function badgeInfo(badge: Badge, description?: string): BadgeInfo {
  const known = KNOWN[badge.slug]
  const family = known?.family ?? 'other'
  return {
    ...badge,
    family,
    level: known?.level ?? 0,
    step: known?.step ?? 1,
    steps: STEPS[family],
    mark: known?.mark,
    description: description ?? known?.description ?? 'Earned on Good Deeds',
  }
}

/** "Kindness · Silver · level 3 of 4" (the ladder line in a badge's details). */
export function ladderText(info: BadgeInfo): string {
  if (info.family === 'other') return FAMILY.other.name
  return `${FAMILY[info.family].name} · ${LEVEL[info.level].name} · level ${info.step} of ${info.steps}`
}

/**
 * The next badge to earn on each ladder (owner only): the lowest-step badge in config that the
 * person doesn't hold yet. Badges the config no longer offers are never suggested.
 */
export function nextBadges(
  earned: Badge[],
  offered: (Badge & { description: string })[],
): BadgeInfo[] {
  const held = new Set(earned.map((b) => b.slug))
  const out: BadgeInfo[] = []
  for (const family of ['recognised', 'witness'] as const) {
    const next = offered
      .filter((b) => KNOWN[b.slug]?.family === family && !held.has(b.slug))
      .sort((a, b) => KNOWN[a.slug].step - KNOWN[b.slug].step)[0]
    if (next) out.push(badgeInfo(next, next.description))
  }
  return out
}

// ----------------------------------------------------------------- unlock memory (per device)

const seenKey = (handle: string) => `gd.badges.seen.${handle}`

/**
 * The badges this device has already celebrated for this person. The API has no award date, so
 * "new" means "not shown on this device yet"; `null` when nothing is stored (or storage fails).
 */
export function readSeenBadges(handle: string): string[] | null {
  try {
    const raw = window.localStorage.getItem(seenKey(handle))
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((s): s is string => typeof s === 'string') : null
  } catch {
    return null
  }
}

export function writeSeenBadges(handle: string, slugs: string[]): void {
  try {
    window.localStorage.setItem(seenKey(handle), JSON.stringify([...new Set(slugs)].sort()))
  } catch {
    // Private mode or blocked storage: the animation may simply play again next time.
  }
}
