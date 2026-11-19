/**
 * Types and calls for the M3 feed and social API (docs/M3-DESIGN.md §3.2, §3.4, §3.4a;
 * backend/app/modules/feed). Reads need a signed-in user; writes need onboarding (D44), except
 * the public deed page and social counts, which work signed out.
 */

import { apiDelete, apiGet, apiPost, apiPut, queryString } from '../../lib/api'
import type { Page, UserSummary } from '../deeds/deedsApi'
import type { PhotoOut } from '../media/mediaApi'

export type { Page } from '../deeds/deedsApi'

const seg = encodeURIComponent

/** `{handle, display_name}`: the only user information in any feed response. */
export type UserRef = UserSummary

// ----------------------------------------------------------------- feed

export type FeedTab = 'nearby' | 'following' | 'top'

/** Rolling windows of the last 7 / 30 / 365 days (Q26). */
export type TopPeriod = 'week' | 'month' | 'year'

export const FEED_TABS: readonly FeedTab[] = ['nearby', 'following', 'top']
export const TOP_PERIODS: readonly TopPeriod[] = ['week', 'month', 'year']
export const DEFAULT_FEED_TAB: FeedTab = 'nearby'
export const DEFAULT_TOP_PERIOD: TopPeriod = 'week'

export type FeedParams = {
  tab: FeedTab
  /** Sent only with `tab: 'top'` (the API answers 422 otherwise); defaults to `week`. */
  period?: TopPeriod
  cursor?: string | null
}

export type PostOut = {
  deed_id: string
  doer: UserRef
  reporter: UserRef
  category: string
  /** Display label from the deeds category options (§3.4b 11); the slug is the fallback. */
  category_label: string
  area_name: string
  occurred_at: string
  published_at: string
  /** The deed text exactly as the reporter wrote it. */
  description: string
  /** Only ready photos; a removed photo is simply absent (Q33). */
  photos: PhotoOut[]
  like_count: number
  comment_count: number
  liked: boolean
  saved: boolean
  following_doer: boolean
  my_role: 'doer' | 'reporter' | null
  /** Top tab only (null elsewhere); show a badge when ≤ `top_badge_ranks`. */
  rank: number | null
  /** `{web_base_url}/d/{deed_id}`. */
  share_url: string
}

/** The caller's hide controls; separate hides per person (Q24). */
export type Visibility = {
  listed: boolean
  hidden_by_me: boolean
  hidden_by_other: boolean
  can_hide: boolean
  can_show: boolean
}

export type PostDetail = PostOut & {
  beneficiaries_count: number | null
  verification: 'qr_scan' | 'invite_accept'
  /** Null unless the caller is the doer or reporter. */
  visibility: Visibility | null
}

export type LikeOut = { liked: boolean; like_count: number }
export type SaveOut = { saved: boolean }

export type ShareTarget = 'whatsapp' | 'copy_link' | 'web_share'

// ----------------------------------------------------------------- comments

export type CommentOut = {
  id: string
  author: UserRef
  body: string
  created_at: string
  mine: boolean
  /** The author, or the deed's doer or reporter. */
  can_delete: boolean
}

/** Comment report reasons (§2.3 `comment_reports.reason`, Q38). */
export type CommentReportReason =
  'threat_or_harassment' | 'hate' | 'personal_info' | 'spam' | 'other'

export type CommentReportRequest = {
  reason: CommentReportReason
  details?: string
}

/** 202, the same answer for a duplicate. */
export type CommentReportOut = { status: 'received' }

// ----------------------------------------------------------------- public page

/** `GET /public/deeds/{id}`: no auth; 404 `deed_unavailable` for anything not listed. */
export type PublicDeed = {
  deed_id: string
  doer: UserRef
  reporter: UserRef
  category: string
  /** Display label from the deeds category options (§3.4b 11); the slug is the fallback. */
  category_label: string
  area_name: string
  occurred_at: string
  description: string
  photos: PhotoOut[]
  like_count: number
  comment_count: number
}

// ----------------------------------------------------------------- social

export type SocialOut = {
  followers: number
  following: number
  /** Null when signed out. */
  is_following: boolean | null
  is_me: boolean
}

/** A row of my followers or following list (Q36: own lists only). */
export type ConnectionOut = {
  user: UserRef
  /** Whether I follow this person. */
  following: boolean
}

// ----------------------------------------------------------------- options

/** `GET /feed/options` (§3.4b 7): limits and labels that follow config. */
export type FeedOptions = {
  comment_max_chars: number
  /** Top ranks shown as a "#{n} this week" badge. */
  top_badge_ranks: number
  top_periods: { id: TopPeriod; days: number }[]
  comment_report_reasons: { id: CommentReportReason; label: string; priority: 1 | 2 | 3 }[]
  report_details_max: number
}

// ----------------------------------------------------------------- one-time notices

/** Known notice keys (a code constant on the server, Q41). */
export type NoticeKey = 'public_feed_intro'

/** One of my comments removed by moderation, not yet dismissed (never names who or why). */
export type CommentNotice = {
  comment_id: string
  deed_id: string
  doer: UserRef
  /** First 80 characters while the text is kept, else null. */
  excerpt: string | null
  removed_at: string
}

// ----------------------------------------------------------------- errors

/** Error codes from the feed endpoints (§3.4 and §3.4a tables). */
export type FeedErrorCode =
  | 'cannot_follow_self'
  | 'invalid_cursor'
  | 'onboarding_required'
  | 'deed_not_found'
  | 'deed_unavailable'
  | 'comment_not_found'
  | 'user_not_found'
  | 'deed_not_published'
  | 'not_shareable'
  | 'following_limit'
  | 'comment_too_long'
  | 'comment_not_kind'
  | 'comment_has_link'
  | 'comment_has_phone'
  | 'conflict_of_interest'
  | 'invalid_reason'
  | 'details_too_long'
  | 'rate_limited'

// ----------------------------------------------------------------- calls

function feedQuery({ tab, period, cursor }: FeedParams): string {
  return queryString({
    tab,
    period: tab === 'top' ? (period ?? DEFAULT_TOP_PERIOD) : undefined,
    cursor,
  })
}

const deedPath = (deedId: string) => `/feed/deeds/${seg(deedId)}`

export const feedApi = {
  options: () => apiGet<FeedOptions>('/feed/options'),

  // feed (§3.4 #1, #2)
  feed: (params: FeedParams) => apiGet<Page<PostOut>>(`/feed${feedQuery(params)}`),
  deed: (deedId: string) => apiGet<PostDetail>(deedPath(deedId)),

  // likes and saves (#3, #4)
  like: (deedId: string) => apiPut<LikeOut>(`${deedPath(deedId)}/like`),
  unlike: (deedId: string) => apiDelete<LikeOut>(`${deedPath(deedId)}/like`),
  save: (deedId: string) => apiPut<SaveOut>(`${deedPath(deedId)}/save`),
  unsave: (deedId: string) => apiDelete<SaveOut>(`${deedPath(deedId)}/save`),

  // comments (#5–#7, §3.4a #13)
  comments: (deedId: string, cursor?: string | null) =>
    apiGet<Page<CommentOut>>(`${deedPath(deedId)}/comments${queryString({ cursor })}`),
  addComment: (deedId: string, body: string) =>
    apiPost<CommentOut>(`${deedPath(deedId)}/comments`, { body }),
  deleteComment: (commentId: string) => apiDelete(`/feed/comments/${seg(commentId)}`),
  reportComment: (commentId: string, data: CommentReportRequest) =>
    apiPost<CommentReportOut>(`/feed/comments/${seg(commentId)}/reports`, data),

  // hide / show (#8): returns the caller's new visibility
  hide: (deedId: string) => apiPost<Visibility>(`${deedPath(deedId)}/hide`),
  show: (deedId: string) => apiPost<Visibility>(`${deedPath(deedId)}/show`),

  // shares (#9): only records the share
  share: (deedId: string, target: ShareTarget) =>
    apiPost<void>(`${deedPath(deedId)}/shares`, { target }),

  // public page (#10): no auth
  publicDeed: (deedId: string) => apiGet<PublicDeed>(`/public/deeds/${seg(deedId)}`),

  // profiles and follows (#11)
  social: (handle: string) => apiGet<SocialOut>(`/users/${seg(handle)}/social`),
  userDeeds: (handle: string, cursor?: string | null) =>
    apiGet<Page<PostOut>>(`/feed/users/${seg(handle)}/deeds${queryString({ cursor })}`),
  follow: (handle: string) => apiPut<SocialOut>(`/users/${seg(handle)}/follow`),
  unfollow: (handle: string) => apiDelete<SocialOut>(`/users/${seg(handle)}/follow`),
  removeFollower: (handle: string) => apiDelete(`/me/followers/${seg(handle)}`),
  myFollowers: (cursor?: string | null) =>
    apiGet<Page<ConnectionOut>>(`/me/followers${queryString({ cursor })}`),
  myFollowing: (cursor?: string | null) =>
    apiGet<Page<ConnectionOut>>(`/me/following${queryString({ cursor })}`),

  // saved (#12)
  saved: (cursor?: string | null) => apiGet<Page<PostOut>>(`/me/saved${queryString({ cursor })}`),

  // one-time notices (Q41) and comment-removed notices (§3.4a)
  notices: () => apiGet<NoticeKey[]>('/feed/me/notices'),
  markNoticeSeen: (key: NoticeKey) => apiPost<void>(`/feed/me/notices/${seg(key)}/seen`),
  commentNotices: () => apiGet<CommentNotice[]>('/feed/me/comment-notices'),
  markCommentNoticeSeen: (commentId: string) =>
    apiPost<void>(`/feed/me/comment-notices/${seg(commentId)}/seen`),
}

/** TanStack Query keys (§6.4): feed data under `['feed', …]`, follow counts under `['social', …]`. */
export const feedKeys = {
  all: ['feed'] as const,
  options: ['feed', 'options'] as const,
  /** `['feed', tab]`; the Top tab adds its period so each period caches separately. */
  list: (tab: FeedTab, period: TopPeriod = DEFAULT_TOP_PERIOD) =>
    tab === 'top' ? (['feed', 'top', period] as const) : (['feed', tab] as const),
  deed: (deedId: string) => ['feed', 'deed', deedId] as const,
  comments: (deedId: string) => ['feed', 'comments', deedId] as const,
  publicDeed: (deedId: string) => ['feed', 'public', deedId] as const,
  userDeeds: (handle: string) => ['feed', 'user-deeds', handle.toLowerCase()] as const,
  saved: ['feed', 'saved'] as const,
  followers: ['feed', 'followers'] as const,
  following: ['feed', 'following'] as const,
  notices: ['feed', 'notices'] as const,
  commentNotices: ['feed', 'comment-notices'] as const,
  social: (handle: string) => ['social', handle.toLowerCase()] as const,
}
