/**
 * Display helpers for the scoring screens (M4-DESIGN §6.3). They only format what the API sends:
 * ranks, points, windows and wording are decided by the server (§6.4).
 */

import { LAUNCH_TIME_ZONE } from '../report/format'
import type { LeaderboardRow } from './scoringApi'

const monthFormat = new Intl.DateTimeFormat('en-IN', {
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})

/** `2026-11` → "November 2026". Unknown input is returned as it is. */
export function formatMonth(month: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(month)
  if (!match) return month
  return monthFormat.format(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1)))
}

const istMonthFormat = new Intl.DateTimeFormat('en-CA', {
  year: 'numeric',
  month: '2-digit',
  timeZone: LAUNCH_TIME_ZONE,
})

/** The current month in IST as `YYYY-MM`, only to label the month picker's choices. */
export function currentIstMonth(now: Date = new Date()): string {
  const parts = istMonthFormat.formatToParts(now)
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? ''
  return `${get('year')}-${get('month')}`
}

/** `count` months ending with `latest` (`YYYY-MM`), newest first. */
export function recentMonths(latest: string, count: number): string[] {
  const [year, month] = latest.split('-').map(Number)
  const months: string[] = []
  for (let i = 0; i < count; i += 1) {
    const date = new Date(Date.UTC(year, month - 1 - i, 1))
    months.push(`${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`)
  }
  return months
}

/** "+10", "−10" (a real minus sign) or "0". */
export function signedPoints(points: number): string {
  if (points > 0) return `+${points}`
  if (points < 0) return `−${Math.abs(points)}`
  return '0'
}

/** "2", or "=2" for a shared place (dense ranking, PO Q58). */
export function rankText(row: Pick<LeaderboardRow, 'rank' | 'shared'>): string {
  return row.shared ? `=${row.rank}` : String(row.rank)
}

/** "1 deed" / "4 deeds". */
export function deedsText(n: number): string {
  return `${n} ${n === 1 ? 'deed' : 'deeds'}`
}
