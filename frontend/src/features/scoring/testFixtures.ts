/** Scoring API fixtures for tests (M4-DESIGN §3.2–§3.4). */

import type {
  Leaderboard,
  LeaderboardRow,
  LedgerEntry,
  MyBoard,
  MyScoring,
  ScoringOptions,
} from './scoringApi'

export const PRIYA = { handle: 'priyas', display_name: 'Priya Sharma' }
export const AHMED = { handle: 'ahmedk', display_name: 'Ahmed Khan' }
export const ASHA = { handle: 'asha', display_name: 'Asha Rao' }
export const RAVI = { handle: 'ravi', display_name: 'Ravi Kumar' }

export const SEED = { slug: 'seed', label: 'Seed' }
export const SPROUT = { slug: 'sprout', label: 'Sprout' }

export function makeOptions(overrides: Partial<ScoringOptions> = {}): ScoringOptions {
  return {
    tiers: [
      { ...SEED, min_points: 0 },
      { ...SPROUT, min_points: 50 },
    ],
    badges: [{ slug: 'first', label: 'First recognition', description: 'Your first deed.' }],
    levels: [
      { kind: 'ward', enabled: true, hero_min_deeds: 3, hero_min_reporters: 2, titled_places: [1] },
      // City is locked for now (PO 2026-11-23, migration 0022): only Area boards are open.
      {
        kind: 'city',
        enabled: false,
        hero_min_deeds: 0,
        hero_min_reporters: 0,
        titled_places: [1, 2, 3],
      },
      {
        kind: 'state',
        enabled: false,
        hero_min_deeds: 0,
        hero_min_reporters: 0,
        titled_places: [],
      },
      {
        kind: 'country',
        enabled: false,
        hero_min_deeds: 0,
        hero_min_reporters: 0,
        titled_places: [],
      },
    ],
    withdraw_window_hours: 24,
    doer_daily_cap: 10,
    reporter_daily_cap: 10,
    reason_labels: {
      verified: 'Deed verified',
      withdrawn: 'Report withdrawn',
      doer_daily_cap: 'Daily limit reached: still counts on your profile',
    },
    ...overrides,
  }
}

export function makeBoard(overrides: Partial<MyBoard> = {}): MyBoard {
  return {
    region: { id: 150, code: 'W150', name: 'Koramangala', kind: 'ward' },
    period_month: '2026-11',
    rank: 4,
    deed_count: 6,
    final: false,
    ...overrides,
  }
}

export function makeMyScoring(overrides: Partial<MyScoring> = {}): MyScoring {
  return {
    tier: SPROUT,
    badges_total: 1,
    titles_total: 0,
    badges: [{ slug: 'first', label: 'First recognition' }],
    titles: [],
    total_points: 124,
    doer_points: 100,
    reporter_points: 14,
    provisional_points: 12,
    boards: [
      makeBoard(),
      makeBoard({
        region: { id: 1, code: 'IN-KA-BLR', name: 'Bangalore', kind: 'city' },
        rank: null,
      }),
    ],
    reporter_today: { used: 3, cap: 10 },
    ...overrides,
  }
}

export function makeEntry(overrides: Partial<LedgerEntry> = {}): LedgerEntry {
  return {
    id: 'e1',
    created_at: '2026-11-20T08:35:00Z',
    deed_id: 'd1',
    role: 'doer',
    kind: 'award',
    points: 10,
    reason: 'verified',
    reason_label: 'Deed verified',
    provisional: true,
    // 3 Dec, 14:05 IST.
    provisional_until: '2026-12-03T08:35:00Z',
    category_label: 'Medical / emergency help',
    doer: PRIYA,
    reporter: AHMED,
    ...overrides,
  }
}

export function makeRow(overrides: Partial<LeaderboardRow> = {}): LeaderboardRow {
  return { rank: 1, shared: false, user: ASHA, tier: SPROUT, deed_count: 9, ...overrides }
}

export function makeLeaderboard(overrides: Partial<Leaderboard> = {}): Leaderboard {
  return {
    region: { code: 'IN-KA-BLR', name: 'Bangalore', kind: 'city' },
    month: '2026-11',
    computed_at: '2026-11-22T10:00:00Z',
    final: false,
    // 2 Dec, 01:00 IST.
    finalizes_at: '2026-12-01T19:30:00Z',
    items: [
      makeRow(),
      makeRow({ rank: 2, shared: true, user: RAVI, tier: SEED, deed_count: 5 }),
      makeRow({ rank: 2, shared: true, user: PRIYA, tier: SEED, deed_count: 5 }),
      makeRow({ rank: 3, user: AHMED, tier: SEED, deed_count: 1 }),
    ],
    me: makeRow({ rank: 2, shared: true, user: PRIYA, tier: SEED, deed_count: 5 }),
    titles: [],
    next_cursor: null,
    ...overrides,
  }
}
