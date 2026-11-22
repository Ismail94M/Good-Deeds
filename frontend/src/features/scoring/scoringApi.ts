/**
 * Types and calls for the M4 scoring API (docs/M4-DESIGN.md §3.2, §3.3 #1–#10;
 * §3.4 clarifications; backend/app/modules/scoring). Every read needs a signed-in user except the
 * public profile scoring (#4); there are no member writes.
 *
 * Privacy (D69, PO Q55): points are shown only to their owner (`MyScoring`, `LedgerEntry`,
 * `DeedPoints`). The public shapes (`PublicScoring`, `LeaderboardRow`, heroes) never carry
 * points or rank scores, so never render points on another person's screen.
 *
 * Nothing about windows, caps or tiers is computed here: wording comes from `/scoring/options`
 * and from `provisional_until` / `withdrawable_until` (§6.4).
 */

import { apiGet, queryString } from '../../lib/api'
import type { UserSummary } from '../deeds/deedsApi'

const seg = encodeURIComponent

// ----------------------------------------------------------------- shared

/** Seed / Sprout / Tree / Forest; `label` is display text from config (names pending PO). */
export type Tier = { slug: string; label: string }

export type Badge = { slug: string; label: string }

/** A Kindness Hero title, already worded by the server ("Kindness Hero · {ward} · {month}"). */
export type HeroTitle = {
  label: string
  /** `YYYY-MM`. */
  period_month: string
  region_name: string
  place: number
}

/** The levels of the location hierarchy (D65). Only levels enabled in config have boards. */
export type RegionKind = 'country' | 'state' | 'city' | 'ward'

export type LedgerRole = 'doer' | 'reporter'
export type LedgerKind = 'award' | 'decision' | 'reversal'

/** `points_ledger.reason` values (§2.3). The server words them; `reason` is the slug. */
export type LedgerReason =
  | 'verified'
  | 'promoted'
  | 'edited'
  | 'prize_rank_changed'
  | 'photo_evidence'
  | 'doer_daily_cap'
  | 'reporter_daily_cap'
  | 'withdrawn'
  | 'rejected'
  | 'counts_for_points_lost'

// ----------------------------------------------------------------- #1 options

/**
 * One region level (§3.4 item 11), e.g. a ward needs 3 deeds from 2 different people for a
 * Hero and titles place 1; the city titles places 1–3 with no minimum. The Hero fields are
 * optional so older replies still work (the copy then falls back to general wording).
 */
export type LevelOption = {
  kind: RegionKind
  enabled: boolean
  hero_min_deeds?: number
  hero_min_reporters?: number
  /** Titled places, sorted, e.g. ward [1], city [1, 2, 3]. */
  titled_places?: number[]
}

/**
 * `GET /scoring/options` (#1, shape per §3.4 item 1). All values come from config.
 */
export type ScoringOptions = {
  tiers: (Tier & { min_points: number })[]
  badges: (Badge & { description: string })[]
  /** Every region level, whether it has boards, and its Hero minimums (§3.4 item 11). */
  levels: LevelOption[]
  withdraw_window_hours: number
  doer_daily_cap: number
  reporter_daily_cap: number
  /** Display text per ledger reason slug (`LedgerReason`), e.g. for the deed points line. */
  reason_labels: Partial<Record<string, string>>
}

// ----------------------------------------------------------------- #2 / #4 profile scoring

/** `GET /scoring/users/{handle}` (#4): public, no login needed (§3.4 item 4), no points. */
export type PublicScoring = {
  tier: Tier
  badges_total: number
  titles_total: number
  badges: Badge[]
  titles: HeroTitle[]
}

/** One of my board places on the My points cards (§6.3). */
export type MyBoard = {
  /** `code` links the card to `/leaderboard/{code}` (§3.4 item 7). */
  region: { id: number; code: string; name: string; kind: RegionKind }
  /** `YYYY-MM`. */
  period_month: string
  /** Null when I'm not on that board yet. */
  rank: number | null
  deed_count: number
  final: boolean
}

/** `GET /scoring/me` (#2): owner only. */
export type MyScoring = PublicScoring & {
  /** Doer + reporter points, summed by the server (§3.4 item 5): never add them up here. */
  total_points: number
  doer_points: number
  reporter_points: number
  /** The part of my points still inside a deed's 24 h window. */
  provisional_points: number
  boards: MyBoard[]
  reporter_today: { used: number; cap: number }
}

// ----------------------------------------------------------------- #3 ledger

/** One history row of My points (#3). Category and names only, never the description. */
export type LedgerEntry = {
  id: string
  created_at: string
  deed_id: string
  role: LedgerRole
  kind: LedgerKind
  /** Positive award, 0 decision, negative reversal. */
  points: number
  reason: LedgerReason
  /** Display text for `reason`, from the server (§3.4 item 6): never map slugs here. */
  reason_label: string
  provisional: boolean
  provisional_until: string | null
  category_label: string
  doer: UserSummary | null
  reporter: UserSummary | null
}

export type LedgerPage = { items: LedgerEntry[]; next_cursor: string | null }

export type LedgerParams = { role?: LedgerRole | null; cursor?: string | null }

// ----------------------------------------------------------------- #5 deed points

/** `GET /scoring/deeds/{deed_id}` (#5): the caller's own row only (doer or reporter). */
export type DeedPoints = {
  points: number
  reason: LedgerReason
  /** Display text for `reason`, from the server (as on `LedgerEntry`). */
  reason_label: string
  capped: boolean
  provisional_until: string | null
}

// ----------------------------------------------------------------- #6–#8 leaderboards

/** One leaderboard row: rank, name, tier and verified-deed count; never points (D69). */
export type LeaderboardRow = {
  rank: number
  /** A shared place, shown as "=2" (dense ranking, Q58). */
  shared: boolean
  user: UserSummary
  tier: Tier
  deed_count: number
}

/** A titled place on a final board. */
export type BoardTitle = { place: number; label: string; users: UserSummary[] }

/** `GET /leaderboards` (#6). Provisional until `final`; `titles` only on final boards. */
export type Leaderboard = {
  region: { code: string; name: string; kind: RegionKind }
  /** `YYYY-MM`. */
  month: string
  /** Null while the board has no snapshot yet (an empty board). */
  computed_at: string | null
  final: boolean
  /**
   * When a provisional board becomes final (the month's finalization cutoff, §3.4 item 10),
   * for "Provisional until 2 Dec, 01:00 IST"; null once final. Without it the banner says
   * "Provisional" with no date, since the client never computes the cutoff (§6.4).
   */
  finalizes_at?: string | null
  items: LeaderboardRow[]
  /** My own row, pinned; null when I'm not on this board. */
  me: LeaderboardRow | null
  titles: BoardTitle[]
  next_cursor: string | null
}

export type LeaderboardParams = {
  /** A region code; omitted = the city board. */
  region?: string | null
  /** `YYYY-MM`; omitted = the current IST month. */
  month?: string | null
  cursor?: string | null
}

/** One ward-picker result (#7). */
export type RegionOption = { code: string; name: string }

export type RegionSearchParams = { kind?: RegionKind; q?: string }

/** `GET /leaderboards/heroes` (#8, §3.4 item 2): final months only, newest first. */
export type HeroMonth = { period_month: string; titles: BoardTitle[] }

export type Heroes = {
  region: { code: string; name: string; kind: RegionKind }
  items: HeroMonth[]
}

export type HeroesParams = { region?: string | null; year?: number | null }

// ----------------------------------------------------------------- #9–#10 admin

/** One evaluation of a deed (rule-based live, or AI shadow in M4b); scores vary by method. */
export type AdminEvaluation = {
  id: string
  method: string
  status: string
  mode: string
  created_at: string
  config_version: number
  [score: string]: unknown
}

/** `GET /admin/scoring/deeds/{deed_id}` (#9, admin only; §3.4 item 3). */
export type AdminDeedScoring = {
  deed_id: string
  ledger: (LedgerEntry & {
    breakdown: Record<string, unknown>
    config_version: number
    reverses_entry_id: string | null
  })[]
  evaluations: AdminEvaluation[]
}

/** `GET /admin/scoring/months/{YYYY-MM}` (#10, admin only; §3.4 item 3): finalization status. */
export type MonthFinalization = {
  period_month: string
  cutoff_at: string
  pending_scoring_events: number
  finalized_at: string | null
  final_snapshots: number
}

// ----------------------------------------------------------------- errors

export type ScoringErrorCode = 'invalid_cursor' | 'invalid_month' | 'not_found' | 'rate_limited'

// ----------------------------------------------------------------- calls

export const scoringApi = {
  options: () => apiGet<ScoringOptions>('/scoring/options'),
  me: () => apiGet<MyScoring>('/scoring/me'),
  ledger: ({ role, cursor }: LedgerParams = {}) =>
    apiGet<LedgerPage>(`/scoring/me/ledger${queryString({ role, cursor })}`),
  user: (handle: string) => apiGet<PublicScoring>(`/scoring/users/${seg(handle)}`),
  deed: (deedId: string) => apiGet<DeedPoints>(`/scoring/deeds/${seg(deedId)}`),
  leaderboard: ({ region, month, cursor }: LeaderboardParams = {}) =>
    apiGet<Leaderboard>(`/leaderboards${queryString({ region, month, cursor })}`),
  regions: ({ kind = 'ward', q }: RegionSearchParams = {}) =>
    apiGet<RegionOption[]>(`/leaderboards/regions${queryString({ kind, q })}`),
  heroes: ({ region, year }: HeroesParams = {}) =>
    apiGet<Heroes>(`/leaderboards/heroes${queryString({ region, year })}`),
  adminDeed: (deedId: string) => apiGet<AdminDeedScoring>(`/admin/scoring/deeds/${seg(deedId)}`),
  adminMonth: (month: string) => apiGet<MonthFinalization>(`/admin/scoring/months/${seg(month)}`),
}

/**
 * TanStack Query keys (§6.4): my scoring under `['scoring', 'me']`, boards under
 * `['leaderboard', region, month]` (null = the API default: city board, current IST month).
 */
export const scoringKeys = {
  all: ['scoring'] as const,
  options: ['scoring', 'options'] as const,
  me: ['scoring', 'me'] as const,
  ledger: (role: LedgerRole | null = null) => ['scoring', 'me', 'ledger', role ?? 'all'] as const,
  user: (handle: string) => ['scoring', 'user', handle.toLowerCase()] as const,
  deed: (deedId: string) => ['scoring', 'deed', deedId] as const,
  leaderboard: (region: string | null = null, month: string | null = null) =>
    ['leaderboard', region, month] as const,
  regions: (kind: RegionKind, q: string) => ['scoring', 'regions', kind, q.trim()] as const,
  heroes: (region: string | null = null, year: number | null = null) =>
    ['scoring', 'heroes', region, year] as const,
  adminDeed: (deedId: string) => ['admin', 'scoring', 'deed', deedId] as const,
  adminMonth: (month: string) => ['admin', 'scoring', 'month', month] as const,
}
