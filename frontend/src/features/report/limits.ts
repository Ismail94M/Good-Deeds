/**
 * Monthly-limit warnings (M2-DESIGN D19, D20, §5.5). The limits, the remaining counts and the
 * warning thresholds (`reporter_monthly_warn_at`, `reporter_doer_monthly_warn_at`) all come from
 * the server's `deeds` config via `GET /deeds/options`.
 */

import type { DeedOptions, ScanOut } from '../deeds/deedsApi'
import { firstName, formatResetDate } from './format'

/** `ok`: no banner; `near`: a warning; `full`: the limit is reached and recording is blocked. */
export type TotalLimitState = 'ok' | 'near' | 'full'

/** `warnAt` is `reporter_monthly_warn_at`: warn when this many or fewer are left in total. */
export function totalLimitState(remaining: number, warnAt: number): TotalLimitState {
  if (remaining <= 0) return 'full'
  if (remaining <= warnAt) return 'near'
  return 'ok'
}

/**
 * The deed form warns only when 1..`warnAt` are left (0 is refused by the server). `warnAt` is
 * `reporter_doer_monthly_warn_at`.
 */
export function showPairWarning(remaining: number, warnAt: number): boolean {
  return remaining >= 1 && remaining <= warnAt
}

/**
 * Banner text. `lead` is shown bold, `rest` after it. `info` is the blue banner (a status),
 * `warning` the amber one; `blocking` banners are announced as alerts.
 */
export type LimitNotice = {
  tone: 'info' | 'warning'
  blocking: boolean
  lead: string
  rest: string
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

function resetsOn(iso: string): string {
  const date = formatResetDate(iso)
  return date ? `Resets on ${date}.` : 'Resets at the start of next month.'
}

/** Hub banner from `GET /deeds/options`, or null when more than `reporter_monthly_warn_at` are left. */
export function totalLimitNotice(
  options: Pick<
    DeedOptions,
    | 'reports_remaining_this_month'
    | 'reporter_monthly_limit'
    | 'reporter_monthly_warn_at'
    | 'month_resets_at'
  >,
): LimitNotice | null {
  const remaining = options.reports_remaining_this_month
  const state = totalLimitState(remaining, options.reporter_monthly_warn_at)
  if (state === 'ok') return null
  if (state === 'near') {
    return {
      tone: 'info',
      blocking: false,
      lead: `${remaining} ${plural(remaining, 'deed', 'deeds')} left this month.`,
      rest: resetsOn(options.month_resets_at),
    }
  }
  const date = formatResetDate(options.month_resets_at)
  return {
    tone: 'warning',
    blocking: true,
    lead: `You've recorded ${options.reporter_monthly_limit} deeds this month, the maximum.`,
    rest: date ? `You can record more from ${date}.` : 'You can record more next month.',
  }
}

/**
 * Deed-form warning from the scan, or null unless 1..`warnAt` reports are left. `warnAt` is
 * `reporter_doer_monthly_warn_at` from `GET /deeds/options`.
 */
export function pairLimitNotice(
  scan: Pick<ScanOut, 'doer' | 'pair_reports_remaining' | 'month_resets_at'>,
  warnAt: number,
): LimitNotice | null {
  const remaining = scan.pair_reports_remaining
  if (!showPairWarning(remaining, warnAt)) return null
  const name = firstName(scan.doer.display_name)
  return {
    tone: 'warning',
    blocking: false,
    lead: `You can record ${name} ${remaining} more ${plural(remaining, 'time', 'times')} this month.`,
    rest: resetsOn(scan.month_resets_at),
  }
}
