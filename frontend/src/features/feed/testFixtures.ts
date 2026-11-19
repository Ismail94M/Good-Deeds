/** Sample data for the feed tests, shaped as M3-DESIGN §3.2. */

import type { PhotoOut } from '../media/mediaApi'
import type { CommentOut, FeedOptions, PostDetail, PostOut, PublicDeed } from './feedApi'

export const AHMED = { handle: 'ahmedk', display_name: 'Ahmed Khan' }
export const PRIYA = { handle: 'priya', display_name: 'Priya Sharma' }
export const RAHUL = { handle: 'rahulv', display_name: 'Rahul Verma' }

export const FEED_OPTIONS: FeedOptions = {
  comment_max_chars: 300,
  top_badge_ranks: 3,
  top_periods: [
    { id: 'week', days: 7 },
    { id: 'month', days: 30 },
    { id: 'year', days: 365 },
  ],
  comment_report_reasons: [
    { id: 'threat_or_harassment', label: 'Threatening or harassing', priority: 1 },
    { id: 'hate', label: 'Hateful', priority: 2 },
    { id: 'personal_info', label: 'Shares someone’s personal information', priority: 1 },
    { id: 'spam', label: 'Spam or scam', priority: 3 },
    { id: 'other', label: 'Something else', priority: 3 },
  ],
  report_details_max: 500,
}

/** Far in the future, so no refresh timer fires during a test. */
const FAR = '2099-01-01T00:00:00Z'

export function makePhoto(n: number, overrides: Partial<PhotoOut> = {}): PhotoOut {
  return {
    id: `p${n}`,
    thumb_url: `https://storage.test/p${n}-thumb.webp`,
    display_url: `https://storage.test/p${n}-display.webp`,
    width: 1280,
    height: 960,
    urls_expire_at: FAR,
    ...overrides,
  }
}

export function makePost(overrides: Partial<PostOut> = {}): PostOut {
  const deedId = overrides.deed_id ?? 'd1'
  return {
    deed_id: deedId,
    doer: AHMED,
    reporter: RAHUL,
    category: 'helping_elderly',
    category_label: 'Helping an elderly person',
    area_name: 'Koramangala',
    occurred_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    published_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    description: 'Helped an elderly man cross the busy junction at Silk Board.',
    photos: [],
    like_count: 24,
    comment_count: 5,
    liked: false,
    saved: false,
    following_doer: false,
    my_role: null,
    rank: null,
    share_url: `http://localhost:5173/d/${deedId}`,
    ...overrides,
  }
}

export function makeDetail(overrides: Partial<PostDetail> = {}): PostDetail {
  return {
    ...makePost(overrides),
    beneficiaries_count: 1,
    verification: 'qr_scan',
    visibility: null,
    ...overrides,
  }
}

export function makeComment(n: number, overrides: Partial<CommentOut> = {}): CommentOut {
  return {
    id: `c${n}`,
    author: RAHUL,
    body: `Comment ${n}`,
    created_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    mine: false,
    can_delete: false,
    ...overrides,
  }
}

export function makePublic(overrides: Partial<PublicDeed> = {}): PublicDeed {
  return {
    deed_id: 'd1',
    doer: AHMED,
    reporter: PRIYA,
    category: 'helping_elderly',
    category_label: 'Helping an elderly person',
    area_name: 'Koramangala',
    occurred_at: '2026-11-16T10:00:00Z',
    description: 'Helped an elderly man cross the busy junction at Silk Board.',
    photos: [],
    like_count: 24,
    comment_count: 5,
    ...overrides,
  }
}

export function page<T>(items: T[], next_cursor: string | null = null) {
  return { body: { items, next_cursor } }
}
