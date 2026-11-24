/** Test data for the admin review screens (used only by `*.test.tsx`). */

import type {
  CommentReportDetail,
  CommentReportItem,
  PhotoReportDetail,
  PhotoReportItem,
} from './adminApi'

export const AHMED = { handle: 'ahmedk', display_name: 'Ahmed Khan' }
export const PRIYA = { handle: 'priya', display_name: 'Priya Sharma' }
export const RAHUL = { handle: 'rahulv', display_name: 'Rahul Verma' }
export const MOD = { handle: 'mod', display_name: 'Meera Mod' }

export function makePhotoReportItem(overrides: Partial<PhotoReportItem> = {}): PhotoReportItem {
  return {
    case_id: 'case-1',
    priority: 2,
    top_reason: 'not_consented',
    report_count: 2,
    opened_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    oldest_report_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    photo_thumb_url: 'https://storage.test/photos/p1/thumb.webp?sig=1',
    deed: { doer: AHMED, category: 'helping_elderly', area_name: 'Koramangala' },
    ...overrides,
  }
}

type DetailOverrides = {
  case?: Partial<PhotoReportDetail['case']>
  photo?: Partial<PhotoReportDetail['photo']>
  deed?: Partial<PhotoReportDetail['deed']>
  reports?: PhotoReportDetail['reports']
  decisions?: PhotoReportDetail['decisions']
}

export function makePhotoReportDetail(overrides: DetailOverrides = {}): PhotoReportDetail {
  const hoursAgo = (h: number) => new Date(Date.now() - h * 60 * 60 * 1000).toISOString()
  return {
    case: {
      case_id: 'case-1',
      status: 'open',
      priority: 2,
      top_reason: 'not_consented',
      report_count: 2,
      opened_at: hoursAgo(3),
      resolved_at: null,
      decision: null,
      removal_reason: null,
      ...overrides.case,
    },
    photo: {
      id: 'p1',
      display_url: 'https://storage.test/photos/p1/display.webp?sig=1',
      position: 2,
      photo_count: 3,
      uploaded_at: '2026-11-18T05:12:00Z',
      uploader: PRIYA,
      blurred_count: 0,
      state: 'visible',
      restorable_until: null,
      purged_at: null,
      ...overrides.photo,
    },
    deed: {
      doer: AHMED,
      reporter: PRIYA,
      category: 'helping_elderly',
      area_name: 'Koramangala',
      status: 'verified',
      listed: true,
      ...overrides.deed,
    },
    reports: overrides.reports ?? [
      {
        reason: 'not_consented',
        details: 'This is my father. He didn’t want his photo online.',
        reporter: { kind: 'visitor' },
        created_at: hoursAgo(3),
      },
      {
        reason: 'not_consented',
        details: null,
        reporter: { kind: 'member', handle: 'rahulv' },
        created_at: hoursAgo(1),
      },
    ],
    decisions: overrides.decisions ?? [],
  }
}

export function makeCommentReportItem(
  overrides: Partial<CommentReportItem> = {},
): CommentReportItem {
  return {
    case_id: 'cc-1',
    priority: 1,
    top_reason: 'threat_or_harassment',
    report_count: 2,
    opened_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    oldest_report_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    comment_excerpt: 'You should be ashamed, I know where you live',
    author: RAHUL,
    deed: { doer: AHMED, category: 'helping_elderly', area_name: 'Koramangala' },
    ...overrides,
  }
}

type CommentDetailOverrides = {
  case?: Partial<CommentReportDetail['case']>
  comment?: Partial<CommentReportDetail['comment']>
  deed?: Partial<CommentReportDetail['deed']>
  reports?: CommentReportDetail['reports']
  decisions?: CommentReportDetail['decisions']
  author_removed_last_90_days?: number
}

export function makeCommentReportDetail(
  overrides: CommentDetailOverrides = {},
): CommentReportDetail {
  const hoursAgo = (h: number) => new Date(Date.now() - h * 60 * 60 * 1000).toISOString()
  return {
    case: {
      case_id: 'cc-1',
      status: 'open',
      priority: 1,
      top_reason: 'threat_or_harassment',
      report_count: 2,
      opened_at: hoursAgo(2),
      resolved_at: null,
      decision: null,
      ...overrides.case,
    },
    comment: {
      id: 'cm1',
      body: 'You should be ashamed, I know where you live.',
      author: RAHUL,
      created_at: '2026-11-18T05:12:00Z',
      state: 'visible',
      restorable_until: null,
      ...overrides.comment,
    },
    deed: {
      doer: AHMED,
      reporter: PRIYA,
      category: 'helping_elderly',
      area_name: 'Koramangala',
      listed: true,
      ...overrides.deed,
    },
    reports: overrides.reports ?? [
      {
        reason: 'threat_or_harassment',
        details: 'This is a threat.',
        reporter: { kind: 'member', handle: 'ahmedk' },
        created_at: hoursAgo(2),
      },
      {
        reason: 'hate',
        details: null,
        reporter: { kind: 'member', handle: 'priya' },
        created_at: hoursAgo(1),
      },
    ],
    decisions: overrides.decisions ?? [],
    author_removed_last_90_days: overrides.author_removed_last_90_days ?? 2,
  }
}
