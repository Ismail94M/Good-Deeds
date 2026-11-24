import { describe, expect, it } from 'vitest'
import { mockApi } from '../../test/utils'
import {
  adminApi,
  adminKeys,
  type CommentReportDetail,
  type CommentReportItem,
  type PhotoReportDetail,
  type PhotoReportItem,
} from './adminApi'

const AHMED = { handle: 'ahmed', display_name: 'Ahmed Khan' }
const PRIYA = { handle: 'priya', display_name: 'Priya Sharma' }
const ADMIN = { handle: 'mod', display_name: 'Mod' }

const PHOTO_ITEM: PhotoReportItem = {
  case_id: 'case-1',
  priority: 1,
  top_reason: 'child_no_guardian_consent',
  report_count: 2,
  opened_at: '2026-11-19T08:00:00Z',
  oldest_report_at: '2026-11-19T08:00:00Z',
  photo_thumb_url: 'https://storage.test/t',
  deed: { doer: AHMED, category: 'animals', area_name: 'Koramangala' },
}

const PHOTO_DETAIL: PhotoReportDetail = {
  case: {
    case_id: 'case-1',
    status: 'resolved',
    priority: 1,
    top_reason: 'child_no_guardian_consent',
    report_count: 2,
    opened_at: '2026-11-19T08:00:00Z',
    resolved_at: '2026-11-19T09:00:00Z',
    decision: 'removed',
    removal_reason: 'child_no_guardian_consent',
  },
  photo: {
    id: 'p1',
    display_url: 'https://storage.test/held/p1',
    position: 1,
    photo_count: 2,
    uploaded_at: '2026-11-19T07:00:00Z',
    uploader: PRIYA,
    blurred_count: 0,
    state: 'removed',
    restorable_until: '2027-02-17T09:00:00Z',
    purged_at: null,
  },
  deed: {
    doer: AHMED,
    reporter: PRIYA,
    category: 'animals',
    area_name: 'Koramangala',
    status: 'verified',
    listed: true,
  },
  reports: [
    {
      reason: 'child_no_guardian_consent',
      details: null,
      reporter: { kind: 'visitor' },
      created_at: '2026-11-19T08:00:00Z',
    },
    {
      reason: 'not_consented',
      details: 'That is me',
      reporter: { kind: 'member', handle: 'rahul' },
      created_at: '2026-11-19T08:10:00Z',
    },
  ],
  decisions: [
    { action: 'remove', note: 'Child shown', actor: ADMIN, created_at: '2026-11-19T09:00:00Z' },
  ],
}

const page = <T>(items: T[]) => ({ items, next_cursor: null, open_count: 3, resolved_count: 7 })

function urlOf(api: ReturnType<typeof mockApi>, n: number): string {
  return String(api.spy.mock.calls[n][0])
}

describe('adminApi: photo reports', () => {
  it('lists the queue by status with counts', async () => {
    const api = mockApi({ 'GET /admin/photo-reports': { body: page([PHOTO_ITEM]) } })
    await expect(adminApi.photoReports({ status: 'open' })).resolves.toEqual(page([PHOTO_ITEM]))
    await adminApi.photoReports({ status: 'resolved', cursor: 'c2' })
    expect(urlOf(api, 0)).toBe('/api/admin/photo-reports?status=open')
    expect(urlOf(api, 1)).toBe('/api/admin/photo-reports?status=resolved&cursor=c2')
  })

  it('surfaces 403 forbidden for a member', async () => {
    mockApi({
      'GET /admin/photo-reports': { status: 403, body: { detail: 'x', code: 'forbidden' } },
    })
    await expect(adminApi.photoReports({ status: 'open' })).rejects.toMatchObject({
      status: 403,
      code: 'forbidden',
    })
  })

  it('reads a case and decides it', async () => {
    const api = mockApi({
      'GET /admin/photo-reports/case-1': { body: PHOTO_DETAIL },
      'POST /admin/photo-reports/case-1/decision': { body: PHOTO_DETAIL },
    })
    await expect(adminApi.photoReport('case-1')).resolves.toEqual(PHOTO_DETAIL)
    const decision = {
      action: 'remove' as const,
      note: 'Child shown',
      removal_reason: 'child_no_guardian_consent' as const,
    }
    await expect(adminApi.decidePhotoReport('case-1', decision)).resolves.toEqual(PHOTO_DETAIL)
    expect(api.calls('POST /admin/photo-reports/case-1/decision')[0].body).toEqual(decision)
  })

  it.each([
    [403, 'conflict_of_interest'],
    [409, 'case_not_open'],
  ])('surfaces %i %s on a decision', async (status, code) => {
    mockApi({
      'POST /admin/photo-reports/case-1/decision': { status, body: { detail: 'x', code } },
    })
    await expect(
      adminApi.decidePhotoReport('case-1', { action: 'keep', note: 'Looks fine' }),
    ).rejects.toMatchObject({ status, code })
  })

  it('restores a photo with a note', async () => {
    const api = mockApi({ 'POST /admin/photos/p1/restore': { body: PHOTO_DETAIL } })
    await expect(adminApi.restorePhoto('p1', { note: 'Consent given' })).resolves.toEqual(
      PHOTO_DETAIL,
    )
    expect(api.calls('POST /admin/photos/p1/restore')[0].body).toEqual({ note: 'Consent given' })
  })

  it('surfaces 409 not_restorable', async () => {
    mockApi({
      'POST /admin/photos/p1/restore': {
        status: 409,
        body: { detail: 'x', code: 'not_restorable' },
      },
    })
    await expect(adminApi.restorePhoto('p1', { note: 'Again' })).rejects.toMatchObject({
      code: 'not_restorable',
    })
  })
})

describe('adminApi: comment reports', () => {
  const ITEM: CommentReportItem = {
    case_id: 'cc-1',
    priority: 2,
    top_reason: 'hate',
    report_count: 1,
    opened_at: '2026-11-19T08:00:00Z',
    oldest_report_at: '2026-11-19T08:00:00Z',
    comment_excerpt: 'Something hateful',
    author: PRIYA,
    deed: { doer: AHMED, category: 'animals', area_name: 'Koramangala' },
  }
  const DETAIL: CommentReportDetail = {
    case: {
      case_id: 'cc-1',
      status: 'open',
      priority: 2,
      top_reason: 'hate',
      report_count: 1,
      opened_at: '2026-11-19T08:00:00Z',
      resolved_at: null,
      decision: null,
    },
    comment: {
      id: 'c1',
      body: 'Something hateful',
      author: PRIYA,
      created_at: '2026-11-19T07:00:00Z',
      state: 'visible',
      restorable_until: null,
    },
    deed: { doer: AHMED, reporter: PRIYA, category: 'animals', area_name: 'K', listed: true },
    reports: [
      {
        reason: 'hate',
        details: null,
        reporter: { kind: 'member', handle: 'rahul' },
        created_at: '2026-11-19T08:00:00Z',
      },
    ],
    decisions: [],
    author_removed_last_90_days: 0,
  }

  it('lists, reads, decides and restores', async () => {
    const api = mockApi({
      'GET /admin/comment-reports': { body: page([ITEM]) },
      'GET /admin/comment-reports/cc-1': { body: DETAIL },
      'POST /admin/comment-reports/cc-1/decision': { body: DETAIL },
      'POST /admin/comments/c1/restore': { body: DETAIL },
    })
    await expect(adminApi.commentReports({ status: 'open' })).resolves.toEqual(page([ITEM]))
    expect(urlOf(api, 0)).toBe('/api/admin/comment-reports?status=open')
    await expect(adminApi.commentReport('cc-1')).resolves.toEqual(DETAIL)

    await expect(
      adminApi.decideCommentReport('cc-1', { action: 'remove', note: 'Hateful words' }),
    ).resolves.toEqual(DETAIL)
    expect(api.calls('POST /admin/comment-reports/cc-1/decision')[0].body).toEqual({
      action: 'remove',
      note: 'Hateful words',
    })

    await expect(adminApi.restoreComment('c1', { note: 'Misread it' })).resolves.toEqual(DETAIL)
    expect(api.calls('POST /admin/comments/c1/restore')[0].body).toEqual({ note: 'Misread it' })
  })

  it('surfaces 422 note_required', async () => {
    mockApi({
      'POST /admin/comment-reports/cc-1/decision': {
        status: 422,
        body: { detail: 'Add a note.', code: 'note_required' },
      },
    })
    await expect(
      adminApi.decideCommentReport('cc-1', { action: 'keep', note: '' }),
    ).rejects.toMatchObject({ status: 422, code: 'note_required' })
  })
})

describe('adminKeys', () => {
  it('keeps every key under admin', () => {
    expect(adminKeys.photoReports('open')).toEqual(['admin', 'photo-reports', 'open'])
    expect(adminKeys.photoReport('case-1')).toEqual(['admin', 'photo-report', 'case-1'])
    expect(adminKeys.commentReports('resolved')).toEqual(['admin', 'comment-reports', 'resolved'])
    expect(adminKeys.commentReport('cc-1')).toEqual(['admin', 'comment-report', 'cc-1'])
  })
})
