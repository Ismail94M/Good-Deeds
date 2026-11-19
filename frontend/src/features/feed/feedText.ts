/** Copy and small formatters for the feed screens (M3-DESIGN §6.3). */

import { firstName, formatShortDate } from '../report/format'
import type { FeedOptions, FeedTab, PostOut, TopPeriod } from './feedApi'

export const TAB_LABELS: Record<FeedTab, string> = {
  nearby: 'Nearby',
  following: 'Following',
  top: 'Top',
}

/** Rolling window lengths (Q26); `GET /feed/options` overrides them when it has loaded. */
const DEFAULT_PERIOD_DAYS: Record<TopPeriod, number> = { week: 7, month: 30, year: 365 }

const PERIOD_WORDS: Record<TopPeriod, string> = {
  week: 'this week',
  month: 'this month',
  year: 'this year',
}

export function periodDays(period: TopPeriod, options?: FeedOptions): number {
  return options?.top_periods.find((p) => p.id === period)?.days ?? DEFAULT_PERIOD_DAYS[period]
}

/** "Top this week (last 7 days)": the period dropdown's option label (Q26). */
export function periodLabel(period: TopPeriod, options?: FeedOptions): string {
  return `Top ${PERIOD_WORDS[period]} (last ${periodDays(period, options)} days)`
}

/** The line under the tabs. */
export function tabIntro(tab: FeedTab, period: TopPeriod, options?: FeedOptions): string {
  if (tab === 'nearby') return 'Good deeds verified around Bangalore, newest first.'
  if (tab === 'following') return 'Deeds by people you follow.'
  return `The most appreciated deeds published in the last ${periodDays(period, options)} days.`
}

/**
 * "#2 this week" for ranks up to `top_badge_ranks`, else null. No badge until the options have
 * loaded, so a rank is never shown with the wrong cut-off.
 */
export function rankChip(
  rank: number | null,
  period: TopPeriod,
  options: FeedOptions | undefined,
): string | null {
  if (rank === null || !options || rank < 1 || rank > options.top_badge_ranks) return null
  return `#${rank} ${PERIOD_WORDS[period]}`
}

/** "recognised by Priya Sharma", or "recognised by you" on my own reports. */
export function recognisedBy(post: Pick<PostOut, 'reporter' | 'my_role'>): string {
  return `recognised by ${post.my_role === 'reporter' ? 'you' : post.reporter.display_name}`
}

/** Generic share text (Q40): no name, area or description travels to other apps. */
export const SHARE_TEXT = 'A good deed was shared with you on Good Deeds.'
export const SHARE_TITLE = 'A good deed was shared with you'

export function shareMessage(url: string): string {
  return `${SHARE_TEXT} ${url}`
}

export function whatsAppHref(url: string): string {
  return `https://wa.me/?text=${encodeURIComponent(shareMessage(url))}`
}

/** "Just now", "12 min ago", "3 h ago", "Yesterday" or "14 Nov" (IST). */
export function timeAgo(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const minutes = Math.floor((now.getTime() - date.getTime()) / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000)
  if (hours < 48 && formatShortDate(iso) === formatShortDate(yesterday.toISOString())) {
    return 'Yesterday'
  }
  return formatShortDate(iso)
}

/** "Helped 1 person" / "Helped 3 people"; null when the count wasn't given. */
export function helpedLine(count: number | null): string | null {
  if (count === null) return null
  return `Helped ${count} ${count === 1 ? 'person' : 'people'}`
}

/** Inline composer errors by API code (§6.3). */
export const COMMENT_ERRORS: Record<string, string> = {
  comment_not_kind:
    'Please keep it kind. Your comment includes a word we don’t allow. Try rewording it.',
  rate_limited: 'You’re commenting very fast — try again in a minute',
  comment_has_link: 'Links aren’t allowed in comments.',
  comment_has_phone: 'Please leave out phone numbers.',
}

export const ONBOARDING_PROMPT = 'Finish setting up to like, comment and follow.'

export { firstName }
