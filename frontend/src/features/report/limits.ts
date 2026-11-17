/**
 * Monthly-limit warnings (M2-DESIGN D19, D20, §5.5). The limits themselves and the remaining
 * counts come from the server; these thresholds only decide when a banner shows.
 */

import type { DeedOptions, ScanOut } from '../deeds/deedsApi'
import { firstName, formatResetDate } from './format'

/** Warn on the hub when this many or fewer reports are left this month (in total). */
export const TOTAL_WARN_AT = 10
/** Warn on the deed form when this many or fewer reports about this doer are left. */
export const PAIR_WARN_AT = 3

/** `ok`: no banner; `near`: a warning; `full`: the limit is reached and recording is blocked. */
export type TotalLimitState = 'ok' | 'near' | 'full'

export function totalLimitState(remaining: number): TotalLimitState {
  if (remaining <= 0) return 'full'
  if (remaining <= TOTAL_WARN_AT) return 'near'
  return 'ok'
}

/** The deed form warns only when 1..PAIR_WARN_AT are left (0 is refused by the server). */
export function showPairWarning(remaining: number): boolean {
  return remaining >= 1 && remaining <= PAIR_WARN_AT
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

/** Hub banner from `GET /deeds/options`, or null when more than TOTAL_WARN_AT are left. */
export function totalLimitNotice(
  options: Pick<
    DeedOptions,
    'reports_remaining_this_month' | 'reporter_monthly_limit' | 'month_resets_at'
  >,
): LimitNotice | null {
  const remaining = options.reports_remaining_this_month
  const state = totalLimitState(remaining)
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

/** Deed-form warning from the scan, or null unless 1..PAIR_WARN_AT reports are left. */
export function pairLimitNotice(
  scan: Pick<ScanOut, 'doer' | 'pair_reports_remaining' | 'month_resets_at'>,
): LimitNotice | null {
  const remaining = scan.pair_reports_remaining
  if (!showPairWarning(remaining)) return null
  const name = firstName(scan.doer.display_name)
  return {
    tone: 'warning',
    blocking: false,
    lead: `You can record ${name} ${remaining} more ${plural(remaining, 'time', 'times')} this month.`,
    rest: resetsOn(scan.month_resets_at),
  }
}
