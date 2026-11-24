/**
 * F17: admin photo reports (docs/M3-DESIGN.md §6.3 "Admin", §8 "Reports and admin"): queue order
 * and chips (Q44), tabs, review with member/visitor labels, note required, Keep, the remove
 * dialog, the done banner, Restore while restorable (Q32) and the conflict-of-interest message.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makeOptions } from '../report/testFixtures'
import { makeMediaOptions } from '../media/testFixtures'
import type { PhotoReportDetail } from './adminApi'
import { makePhotoReportDetail, makePhotoReportItem, MOD } from './testFixtures'

const moderator = () => makeMe({ role: 'moderator', handle: MOD.handle })

function base(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(moderator()),
    'GET /deeds/options': { body: makeOptions() },
    'GET /media/options': { body: makeMediaOptions() },
    ...extra,
  })
}

describe('photo report queue', () => {
  it('lists open reports in API order with chips, reason, deed and age', async () => {
    base({
      'GET /admin/photo-reports': {
        body: {
          items: [
            makePhotoReportItem({
              case_id: 'c-child',
              priority: 1,
              top_reason: 'child_no_guardian_consent',
              report_count: 1,
              oldest_report_at: new Date(Date.now() - 40 * 60_000).toISOString(),
            }),
            makePhotoReportItem({ case_id: 'c-privacy' }),
            makePhotoReportItem({
              case_id: 'c-other',
              priority: 3,
              top_reason: 'other',
              report_count: 1,
              oldest_report_at: new Date(Date.now() - 26 * 60 * 60_000).toISOString(),
            }),
          ],
          next_cursor: null,
          open_count: 3,
          resolved_count: 7,
        },
      },
    })
    renderApp('/admin/photo-reports')
    expect(await screen.findByRole('heading', { name: 'Photo reports' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Admin' })).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: 'Open · 3' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Resolved · 7' })).toBeInTheDocument()
    const rows = screen.getAllByRole('listitem').filter((li) => li.closest('main'))
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Child')
    expect(rows[0]).toHaveTextContent('A child is shown without a parent’s consent')
    expect(rows[0]).toHaveTextContent('1 report · 40 min ago')
    expect(rows[1]).toHaveTextContent('Privacy/Harm')
    await waitFor(() =>
      expect(rows[1]).toHaveTextContent('Ahmed Khan · Helping an elderly person · Koramangala'),
    )
    expect(rows[1]).toHaveTextContent('2 reports · oldest 3 h ago')
    expect(rows[2]).toHaveTextContent('Other')
    expect(rows[2]).toHaveTextContent('Something else')
    expect(rows[2]).toHaveTextContent('yesterday')
    expect(within(rows[1]).getByRole('link', { name: /Review/ })).toHaveAttribute(
      'href',
      '/admin/photo-reports/c-privacy',
    )
  })

  it('switches to Resolved through ?status and shows the empty state', async () => {
    const api = base({
      'GET /admin/photo-reports': {
        body: { items: [], next_cursor: null, open_count: 0, resolved_count: 0 },
      },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/admin/photo-reports')
    expect(await screen.findByText('No open photo reports')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /Resolved/ }))
    expect(router.state.location.search).toBe('?status=resolved')
    expect(await screen.findByText('No resolved reports yet')).toBeInTheDocument()
    const urls = api.spy.mock.calls.map(([input]) => String(input))
    expect(urls).toContain('/api/admin/photo-reports?status=resolved')
  })

  it('loads more with the cursor', async () => {
    const api = base({
      'GET /admin/photo-reports': [
        {
          body: {
            items: [makePhotoReportItem({ case_id: 'c1' })],
            next_cursor: 'n2',
            open_count: 2,
            resolved_count: 0,
          },
        },
        {
          body: {
            items: [makePhotoReportItem({ case_id: 'c2' })],
            next_cursor: null,
            open_count: 2,
            resolved_count: 0,
          },
        },
      ],
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports')
    await user.click(await screen.findByRole('button', { name: 'Show more' }))
    await waitFor(() => expect(screen.getAllByRole('link', { name: /Review/ })).toHaveLength(2))
    const urls = api.spy.mock.calls.map(([input]) => String(input))
    expect(urls).toContain('/api/admin/photo-reports?status=open&cursor=n2')
  })

  it('shows an error with Try again', async () => {
    base({ 'GET /admin/photo-reports': { status: 500, body: { detail: 'Server trouble.' } } })
    renderApp('/admin/photo-reports')
    expect(await screen.findByText('Server trouble.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

function review(detail: PhotoReportDetail, extra: Parameters<typeof mockApi>[0] = {}) {
  return base({ 'GET /admin/photo-reports/case-1': { body: detail }, ...extra })
}

describe('photo report review', () => {
  it('shows the photo, deed, upload record and reports', async () => {
    review(makePhotoReportDetail())
    renderApp('/admin/photo-reports/case-1')
    expect(await screen.findByRole('heading', { name: 'Review photo report' })).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'Reported photo: photo 2 of 3 on Ahmed Khan’s deed' }),
    ).toHaveAttribute('src', 'https://storage.test/photos/p1/display.webp?sig=1')
    expect(screen.getByText('Visible · report open')).toBeInTheDocument()
    expect(screen.getByText(/Photo 2 of 3 · Uploaded 18 Nov 2026, 10:42/)).toBeInTheDocument()
    const deed = screen.getByRole('region', { name: 'Deed' })
    expect(deed).toHaveTextContent('Ahmed Khan, recognised by Priya Sharma (uploaded the photos)')
    await waitFor(() =>
      expect(deed).toHaveTextContent(
        'Helping an elderly person · Koramangala · Verified · Shown in feed',
      ),
    )
    const upload = screen.getByRole('region', { name: 'Upload record' })
    expect(upload).toHaveTextContent('Uploaded by Priya Sharma on 18 Nov 2026, 10:42.')
    expect(upload).toHaveTextContent('No faces were blurred on this photo.')
    const reports = screen.getByRole('region', { name: 'Reports (2)' })
    expect(reports).toHaveTextContent('“This is my father. He didn’t want his photo online.”')
    expect(reports).toHaveTextContent('Visitor on a shared link · 3 h ago')
    expect(reports).toHaveTextContent('Member @rahulv · 1 h ago')
    expect(within(reports).getAllByText('Privacy/Harm')).toHaveLength(2)
    expect(
      screen.getByText(
        'Priya is told the outcome. The deed, its verification and points never change.',
      ),
    ).toBeInTheDocument()
  })

  it('requires a note before Keep or Remove', async () => {
    const api = review(makePhotoReportDetail())
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.click(await screen.findByRole('button', { name: 'Keep photo' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Write a short note (at least 5 characters) before you decide.',
    )
    await user.click(screen.getByRole('button', { name: 'Remove photo' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.calls('POST /admin/photo-reports/case-1/decision')).toHaveLength(0)
  })

  it('keeps the photo and shows the done banner', async () => {
    const decided = makePhotoReportDetail({
      case: { status: 'resolved', decision: 'kept', resolved_at: new Date().toISOString() },
    })
    const api = review(makePhotoReportDetail(), {
      'POST /admin/photo-reports/case-1/decision': { body: decided },
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.type(
      await screen.findByRole('textbox', { name: /Note for the record/ }),
      'Shows a public road, no faces.',
    )
    await user.click(screen.getByRole('button', { name: 'Keep photo' }))
    expect(await screen.findByText('Photo kept.')).toBeInTheDocument()
    expect(api.calls('POST /admin/photo-reports/case-1/decision')[0].body).toEqual({
      action: 'keep',
      note: 'Shows a public road, no faces.',
    })
    expect(screen.getByRole('status')).toHaveTextContent(
      'Priya has been told the outcome. Your note and decision are in the audit log.',
    )
    expect(screen.getByText('Kept after review')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Keep photo' })).toBeNull()
  })

  it('removes after the dialog, with the reason defaulting to the top report', async () => {
    const until = new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).toISOString()
    const removed = makePhotoReportDetail({
      case: {
        status: 'resolved',
        decision: 'removed',
        removal_reason: 'private_details',
        resolved_at: new Date().toISOString(),
      },
      photo: { state: 'removed', restorable_until: until },
    })
    const api = review(makePhotoReportDetail(), {
      'POST /admin/photo-reports/case-1/decision': { body: removed },
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.type(
      await screen.findByRole('textbox', { name: /Note for the record/ }),
      'The man asked for it to be taken down.',
    )
    await user.click(screen.getByRole('button', { name: 'Remove photo' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this photo?' })
    expect(dialog).toHaveTextContent(
      'It disappears from the feed, the deed page and shared links. Priya is told it was removed after review. The deed, its verification and points don’t change. You can restore it later.',
    )
    const reason = within(dialog).getByRole('combobox', { name: 'Reason shown to Priya' })
    expect(reason).toHaveValue('not_consented')
    await user.selectOptions(reason, 'private_details')
    await user.click(within(dialog).getByRole('button', { name: 'Remove photo' }))

    expect(await screen.findByText('Photo removed.')).toBeInTheDocument()
    expect(api.calls('POST /admin/photo-reports/case-1/decision')[0].body).toEqual({
      action: 'remove',
      note: 'The man asked for it to be taken down.',
      removal_reason: 'private_details',
    })
    expect(screen.getByText('Removed')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/Can be restored until \d+ \w+ \d{4}\./)
    expect(screen.getByRole('button', { name: 'Restore photo' })).toBeInTheDocument()
  })

  it('Cancel in the remove dialog sends nothing', async () => {
    const api = review(makePhotoReportDetail())
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.type(
      await screen.findByRole('textbox', { name: /Note for the record/ }),
      'Looks fine to me',
    )
    await user.click(screen.getByRole('button', { name: 'Remove photo' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.calls('POST /admin/photo-reports/case-1/decision')).toHaveLength(0)
  })

  it('shows the conflict-of-interest message', async () => {
    review(makePhotoReportDetail(), {
      'POST /admin/photo-reports/case-1/decision': {
        status: 403,
        body: { detail: 'x', code: 'conflict_of_interest' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.type(
      await screen.findByRole('textbox', { name: /Note for the record/ }),
      'Keeping it up.',
    )
    await user.click(screen.getByRole('button', { name: 'Keep photo' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Another admin needs to review this one: you’re part of this deed.',
    )
  })

  it('restores a removed photo with a note', async () => {
    const until = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString()
    const removed = makePhotoReportDetail({
      case: { status: 'resolved', decision: 'removed', removal_reason: 'not_consented' },
      photo: { state: 'removed', restorable_until: until },
    })
    const restored = makePhotoReportDetail({
      case: { status: 'resolved', decision: 'removed', removal_reason: 'not_consented' },
      photo: { state: 'visible' },
      decisions: [
        {
          action: 'remove',
          note: 'Asked to remove.',
          actor: MOD,
          created_at: '2026-11-18T06:00:00Z',
        },
        {
          action: 'restore',
          note: 'He agreed after all.',
          actor: MOD,
          created_at: '2026-11-19T06:00:00Z',
        },
      ],
    })
    const api = review(removed, { 'POST /admin/photos/p1/restore': { body: restored } })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports/case-1')
    await user.click(await screen.findByRole('button', { name: 'Restore photo' }))
    const dialog = screen.getByRole('dialog', { name: 'Restore this photo?' })
    await user.click(within(dialog).getByRole('button', { name: 'Restore photo' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('at least 5 characters')
    await user.type(within(dialog).getByRole('textbox'), 'He agreed after all.')
    await user.click(within(dialog).getByRole('button', { name: 'Restore photo' }))
    expect(await screen.findByText('Photo restored.')).toBeInTheDocument()
    expect(api.calls('POST /admin/photos/p1/restore')[0].body).toEqual({
      note: 'He agreed after all.',
    })
    expect(screen.queryByRole('button', { name: 'Restore photo' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Decisions' })).toHaveTextContent(
      'Restored by Meera Mod',
    )
  })

  it('says when a removed photo was deleted for good, without Restore', async () => {
    review(
      makePhotoReportDetail({
        case: { status: 'resolved', decision: 'removed', removal_reason: 'other' },
        photo: { state: 'removed', purged_at: '2027-02-17T03:00:00Z' },
      }),
    )
    renderApp('/admin/photo-reports/case-1')
    expect(await screen.findByText('Deleted for good on 17 February 2027.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Restore photo' })).toBeNull()
  })

  it('shows a missing report as unavailable', async () => {
    base({
      'GET /admin/photo-reports/case-1': {
        status: 404,
        body: { detail: 'x', code: 'case_not_found' },
      },
    })
    renderApp('/admin/photo-reports/case-1')
    expect(
      await screen.findByRole('heading', { name: 'This report isn’t available' }),
    ).toBeInTheDocument()
  })
})
