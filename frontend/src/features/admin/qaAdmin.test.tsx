/**
 * QA7 (M3 frontend, admin): the PO rules Q32 (restore only until the retention date), Q34 (roles;
 * the note is required), Q38 (comment restore window) and Q44 (harmful photos rank with privacy:
 * "Privacy/Harm") beyond what PhotoReports / CommentReports tests already pin down.
 */

import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { FEED_OPTIONS } from '../feed/testFixtures'
import { makeMediaOptions } from '../media/testFixtures'
import { makeOptions } from '../report/testFixtures'
import {
  makeCommentReportDetail,
  makePhotoReportDetail,
  makePhotoReportItem,
  MOD,
} from './testFixtures'

function base(
  extra: Parameters<typeof mockApi>[0] = {},
  role: 'moderator' | 'admin' = 'moderator',
) {
  return mockApi({
    ...signedIn(makeMe({ role, handle: MOD.handle })),
    'GET /deeds/options': { body: makeOptions() },
    'GET /media/options': { body: makeMediaOptions() },
    'GET /feed/options': { body: FEED_OPTIONS },
    ...extra,
  })
}

const daysFromNow = (d: number) => new Date(Date.now() + d * 24 * 60 * 60 * 1000).toISOString()

describe('photo reports: chips (Q44)', () => {
  it('an "inappropriate or harmful" report is Privacy/Harm, not Other', async () => {
    base({
      'GET /admin/photo-reports': {
        body: {
          items: [makePhotoReportItem({ priority: 2, top_reason: 'inappropriate' })],
          next_cursor: null,
          open_count: 1,
          resolved_count: 0,
        },
      },
    })
    renderApp('/admin/photo-reports')
    // The first query waits for the auth refresh, `/users/me` and the admin route to mount,
    // which can pass the 1 s default under full-suite load.
    await screen.findByRole('tab', { name: 'Open · 1' }, { timeout: 5000 })
    // Wait for the report row itself (its reason text), not for whichever list item renders
    // first, so the check never runs against a half-rendered page.
    const reason = await screen.findByText('It’s inappropriate or harmful')
    const row = reason.closest('li')
    expect(row).not.toBeNull()
    expect(row!.closest('main')).not.toBeNull()
    expect(row).toHaveTextContent('Privacy/Harm')
    expect(row).toHaveTextContent('It’s inappropriate or harmful')
    expect(row).not.toHaveTextContent(/\bOther\b/)
  })

  it('on the review page each report carries its own reason’s chip', async () => {
    base({
      'GET /admin/photo-reports/case-1': {
        body: makePhotoReportDetail({
          case: { priority: 1, top_reason: 'child_no_guardian_consent' },
          reports: [
            {
              reason: 'child_no_guardian_consent',
              details: null,
              reporter: { kind: 'visitor' },
              created_at: daysFromNow(-0.1),
            },
            {
              reason: 'inappropriate',
              details: null,
              reporter: { kind: 'member', handle: 'rahulv' },
              created_at: daysFromNow(-0.05),
            },
            {
              reason: 'other',
              details: null,
              reporter: { kind: 'member', handle: 'kavya' },
              created_at: daysFromNow(-0.01),
            },
          ],
        }),
      },
    })
    renderApp('/admin/photo-reports/case-1')
    const reports = await screen.findByRole('region', { name: 'Reports (3)' })
    expect(within(reports).getByText('Child')).toBeInTheDocument()
    expect(within(reports).getByText('Privacy/Harm')).toBeInTheDocument()
    expect(within(reports).getByText('Other')).toBeInTheDocument()
  })
})

describe('photo reports: decisions (Q34)', () => {
  it('a note of only spaces is not a note', async () => {
    const api = base({ 'GET /admin/photo-reports/case-1': { body: makePhotoReportDetail() } })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.type(await screen.findByRole('textbox', { name: /Note for the record/ }), '        ')
    await user.click(screen.getByRole('button', { name: 'Keep photo' }))
    expect(screen.getByRole('alert')).toHaveTextContent('at least 5 characters')
    await user.click(screen.getByRole('button', { name: 'Remove photo' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.calls('POST /admin/photo-reports/case-1/decision')).toHaveLength(0)
  })

  it('a note of exactly 5 characters is accepted and sent trimmed', async () => {
    const api = base({
      'GET /admin/photo-reports/case-1': { body: makePhotoReportDetail() },
      'POST /admin/photo-reports/case-1/decision': {
        body: makePhotoReportDetail({ case: { status: 'resolved', decision: 'kept' } }),
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.type(await screen.findByRole('textbox', { name: /Note for the record/ }), '  Fine. ')
    await user.click(screen.getByRole('button', { name: 'Keep photo' }))
    expect(await screen.findByText('Photo kept.')).toBeInTheDocument()
    expect(api.calls('POST /admin/photo-reports/case-1/decision')[0].body).toEqual({
      action: 'keep',
      note: 'Fine.',
    })
  })

  it('an admin (not only a moderator) can open the queue', async () => {
    base(
      {
        'GET /admin/photo-reports': {
          body: { items: [], next_cursor: null, open_count: 0, resolved_count: 0 },
        },
      },
      'admin',
    )
    renderApp('/admin/photo-reports')
    expect(await screen.findByText('No open photo reports')).toBeInTheDocument()
  })

  it('a member opening an admin review URL gets Not found and no admin request is made', async () => {
    const api = mockApi({ ...signedIn(makeMe()) })
    renderApp('/admin/photo-reports/case-1')
    expect(
      await screen.findByRole('heading', { name: /not found|isn’t here|can’t find/i }),
    ).toBeVisible()
    expect(api.requests.some((r) => r.path.startsWith('/admin'))).toBe(false)
  })
})

describe('photo reports: restore window (Q32)', () => {
  const removed = (photo: NonNullable<Parameters<typeof makePhotoReportDetail>[0]>['photo']) =>
    makePhotoReportDetail({
      case: { status: 'resolved', decision: 'removed', removal_reason: 'not_consented' },
      photo: { state: 'removed', ...photo },
    })

  it('inside the window: Restore and "Can be restored until {date}."', async () => {
    base({
      'GET /admin/photo-reports/case-1': { body: removed({ restorable_until: daysFromNow(1) }) },
    })
    renderApp('/admin/photo-reports/case-1')
    expect(await screen.findByRole('button', { name: 'Restore photo' })).toBeInTheDocument()
    expect(screen.getByText(/^Can be restored until \d+ \w+ \d{4}\.$/)).toBeInTheDocument()
  })

  it('after the restore date (not yet purged): no Restore and no "until" line', async () => {
    base({
      'GET /admin/photo-reports/case-1': { body: removed({ restorable_until: daysFromNow(-1) }) },
    })
    renderApp('/admin/photo-reports/case-1')
    expect(await screen.findByText('Photo removed.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Restore photo' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Can be restored until/)).not.toBeInTheDocument()
  })

  it('shows the not_restorable error inside the restore dialog', async () => {
    base({
      'GET /admin/photo-reports/case-1': { body: removed({ restorable_until: daysFromNow(1) }) },
      'POST /admin/photos/p1/restore': {
        status: 409,
        body: { detail: 'This photo can no longer be restored.', code: 'not_restorable' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.click(await screen.findByRole('button', { name: 'Restore photo' }))
    const dialog = screen.getByRole('dialog', { name: 'Restore this photo?' })
    await user.type(within(dialog).getByRole('textbox'), 'Parent agreed.')
    await user.click(within(dialog).getByRole('button', { name: 'Restore photo' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/restored/i)
  })

  it('the review page shows a loading state, then an error with Try again', async () => {
    base({
      'GET /admin/photo-reports/case-1': [
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        { body: makePhotoReportDetail() },
      ],
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    expect(await screen.findByText('Loading this report…')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Review photo report' })).toBeInTheDocument()
  })

  it('the remove dialog is modal, focuses a button and closes on Esc', async () => {
    base({ 'GET /admin/photo-reports/case-1': { body: makePhotoReportDetail() } })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.type(
      await screen.findByRole('textbox', { name: /Note for the record/ }),
      'Asked to remove.',
    )
    const remove = screen.getByRole('button', { name: 'Remove photo' })
    await user.click(remove)
    const dialog = screen.getByRole('dialog', { name: 'Remove this photo?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(remove).toHaveFocus()
  })
})

describe('comment reports: restore window (Q38)', () => {
  it('after the 30-day window: no Restore comment and no "until" line', async () => {
    base(
      {
        'GET /admin/comment-reports/cc-1': {
          body: makeCommentReportDetail({
            case: { status: 'resolved', decision: 'removed' },
            comment: { state: 'removed', restorable_until: daysFromNow(-1) },
          }),
        },
      },
      'admin',
    )
    renderApp('/admin/comment-reports/cc-1')
    expect(await screen.findByRole('heading', { name: /Review comment report/ })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Restore comment' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Can be restored until/)).not.toBeInTheDocument()
  })

  it('a note of only spaces is refused before Keep', async () => {
    const api = base(
      { 'GET /admin/comment-reports/cc-1': { body: makeCommentReportDetail() } },
      'admin',
    )
    const user = userEvent.setup()
    renderApp('/admin/comment-reports/cc-1')
    await user.type(await screen.findByRole('textbox', { name: /Note for the record/ }), '      ')
    await user.click(screen.getByRole('button', { name: /^Keep/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('at least 5 characters')
    expect(api.calls('POST /admin/comment-reports/cc-1/decision')).toHaveLength(0)
  })

  it('the comment queue shows an error with Try again', async () => {
    base(
      {
        'GET /admin/comment-reports': [
          { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
          { body: { items: [], next_cursor: null, open_count: 0, resolved_count: 0 } },
        ],
      },
      'admin',
    )
    const user = userEvent.setup()
    renderApp('/admin/comment-reports')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/No open comment reports/)).toBeInTheDocument()
  })
})
