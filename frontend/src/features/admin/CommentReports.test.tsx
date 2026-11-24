/**
 * F19 (admin side): comment reports (docs/M3-DESIGN.md §3.4a, §3.4b 5, §6.3, §8): queue order and
 * chips, review with the full comment, author history, reports, note required, Keep / Remove, and
 * Restore while restorable.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { FEED_OPTIONS } from '../feed/testFixtures'
import { makeMediaOptions } from '../media/testFixtures'
import { makeOptions } from '../report/testFixtures'
import type { CommentReportDetail } from './adminApi'
import { makeCommentReportDetail, makeCommentReportItem, MOD } from './testFixtures'

function base(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(makeMe({ role: 'admin', handle: MOD.handle })),
    'GET /deeds/options': { body: makeOptions() },
    'GET /media/options': { body: makeMediaOptions() },
    'GET /feed/options': { body: FEED_OPTIONS },
    ...extra,
  })
}

describe('comment report queue', () => {
  it('lists reports with chips, reason, excerpt, author and deed', async () => {
    base({
      'GET /admin/comment-reports': {
        body: {
          items: [
            makeCommentReportItem(),
            makeCommentReportItem({
              case_id: 'cc-2',
              priority: 2,
              top_reason: 'hate',
              report_count: 1,
            }),
            makeCommentReportItem({ case_id: 'cc-3', priority: 3, top_reason: 'spam' }),
          ],
          next_cursor: null,
          open_count: 3,
          resolved_count: 1,
        },
      },
    })
    renderApp('/admin/comment-reports')
    expect(await screen.findByRole('heading', { name: 'Comment reports' })).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: 'Open · 3' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Resolved · 1' })).toBeInTheDocument()
    const rows = screen.getAllByRole('listitem').filter((li) => li.closest('main'))
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Safety')
    expect(rows[0]).toHaveTextContent('Threatening or harassing')
    expect(rows[0]).toHaveTextContent('“You should be ashamed, I know where you live”')
    expect(rows[0]).toHaveTextContent('Rahul Verma on Ahmed Khan’s deed')
    expect(rows[0]).toHaveTextContent('2 reports · oldest 2 h ago')
    expect(rows[1]).toHaveTextContent('Hate')
    expect(rows[1]).toHaveTextContent('1 report · 2 h ago')
    expect(rows[2]).toHaveTextContent('Other')
    expect(rows[2]).toHaveTextContent('Spam or scam')
    expect(within(rows[0]).getByRole('link', { name: /Review/ })).toHaveAttribute(
      'href',
      '/admin/comment-reports/cc-1',
    )
  })

  it('shows the empty state', async () => {
    base({
      'GET /admin/comment-reports': {
        body: { items: [], next_cursor: null, open_count: 0, resolved_count: 0 },
      },
    })
    renderApp('/admin/comment-reports')
    expect(await screen.findByText('No open comment reports')).toBeInTheDocument()
  })
})

function review(detail: CommentReportDetail, extra: Parameters<typeof mockApi>[0] = {}) {
  return base({ 'GET /admin/comment-reports/cc-1': { body: detail }, ...extra })
}

describe('comment report review', () => {
  it('shows the full comment, the author’s history, the deed and the reports', async () => {
    review(makeCommentReportDetail())
    renderApp('/admin/comment-reports/cc-1')
    expect(await screen.findByRole('heading', { name: 'Review comment report' })).toBeVisible()
    const comment = screen.getByRole('region', { name: 'Comment' })
    expect(comment).toHaveTextContent('You should be ashamed, I know where you live.')
    expect(comment).toHaveTextContent('Visible · report open')
    expect(comment).toHaveTextContent('By Rahul Verma (@rahulv)')
    expect(comment).toHaveTextContent('Rahul Verma has had 2 comments removed in the last 90 days.')
    const deed = screen.getByRole('region', { name: 'Deed' })
    expect(deed).toHaveTextContent('Ahmed Khan, recognised by Priya Sharma')
    await waitFor(() =>
      expect(deed).toHaveTextContent('Helping an elderly person · Koramangala · Shown in feed'),
    )
    const reports = screen.getByRole('region', { name: 'Reports (2)' })
    expect(reports).toHaveTextContent('Threatening or harassing')
    expect(reports).toHaveTextContent('“This is a threat.”')
    expect(reports).toHaveTextContent('Member @ahmedk')
    expect(reports).toHaveTextContent('Hateful')
  })

  it('says "1 comment" in the history line', async () => {
    review(makeCommentReportDetail({ author_removed_last_90_days: 1 }))
    renderApp('/admin/comment-reports/cc-1')
    expect(
      await screen.findByText(/Rahul Verma has had 1 comment removed in the last 90 days\./),
    ).toBeInTheDocument()
  })

  it('requires a note, then keeps the comment', async () => {
    const kept = makeCommentReportDetail({ case: { status: 'resolved', decision: 'kept' } })
    const api = review(makeCommentReportDetail(), {
      'POST /admin/comment-reports/cc-1/decision': { body: kept },
    })
    const user = userEvent.setup()
    renderApp('/admin/comment-reports/cc-1')
    await user.click(await screen.findByRole('button', { name: 'Keep comment' }))
    expect(screen.getByRole('alert')).toHaveTextContent('at least 5 characters')
    await user.type(screen.getByRole('textbox', { name: /Note for the record/ }), 'Harsh but ok.')
    await user.click(screen.getByRole('button', { name: 'Keep comment' }))
    expect(await screen.findByText('Comment kept.')).toBeInTheDocument()
    expect(api.calls('POST /admin/comment-reports/cc-1/decision')[0].body).toEqual({
      action: 'keep',
      note: 'Harsh but ok.',
    })
    expect(screen.getByText('Kept after review')).toBeInTheDocument()
  })

  it('removes after confirming and offers Restore until the date', async () => {
    const until = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString()
    const removed = makeCommentReportDetail({
      case: { status: 'resolved', decision: 'removed' },
      comment: { state: 'removed', restorable_until: until },
    })
    const api = review(makeCommentReportDetail(), {
      'POST /admin/comment-reports/cc-1/decision': { body: removed },
    })
    const user = userEvent.setup()
    renderApp('/admin/comment-reports/cc-1')
    await user.type(
      await screen.findByRole('textbox', { name: /Note for the record/ }),
      'A threat against the doer.',
    )
    await user.click(screen.getByRole('button', { name: 'Remove comment' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this comment?' })
    await user.click(within(dialog).getByRole('button', { name: 'Remove comment' }))
    expect(await screen.findByText('Comment removed.')).toBeInTheDocument()
    expect(api.calls('POST /admin/comment-reports/cc-1/decision')[0].body).toEqual({
      action: 'remove',
      note: 'A threat against the doer.',
    })
    expect(screen.getByRole('status')).toHaveTextContent(
      'Rahul has been told it broke our community guidelines.',
    )
    expect(screen.getByRole('status')).toHaveTextContent(/Can be restored until \d+ \w+ \d{4}\./)
    expect(screen.getByRole('button', { name: 'Restore comment' })).toBeInTheDocument()
  })

  it('restores a removed comment with a note', async () => {
    const until = new Date(Date.now() + 10 * 24 * 60 * 60 * 1000).toISOString()
    const removed = makeCommentReportDetail({
      case: { status: 'resolved', decision: 'removed' },
      comment: { state: 'removed', restorable_until: until },
    })
    const restored = makeCommentReportDetail({
      case: { status: 'resolved', decision: 'removed' },
      comment: { state: 'visible' },
    })
    const api = review(removed, { 'POST /admin/comments/cm1/restore': { body: restored } })
    const user = userEvent.setup()
    renderApp('/admin/comment-reports/cc-1')
    await user.click(await screen.findByRole('button', { name: 'Restore comment' }))
    const dialog = screen.getByRole('dialog', { name: 'Restore this comment?' })
    await user.type(within(dialog).getByRole('textbox'), 'Removed by mistake.')
    await user.click(within(dialog).getByRole('button', { name: 'Restore comment' }))
    expect(await screen.findByText('Comment restored.')).toBeInTheDocument()
    expect(api.calls('POST /admin/comments/cm1/restore')[0].body).toEqual({
      note: 'Removed by mistake.',
    })
    expect(screen.queryByRole('button', { name: 'Restore comment' })).toBeNull()
  })

  it('has no Restore once the text is erased', async () => {
    review(
      makeCommentReportDetail({
        case: { status: 'resolved', decision: 'removed' },
        comment: { state: 'removed', body: null, restorable_until: null },
      }),
    )
    renderApp('/admin/comment-reports/cc-1')
    expect(await screen.findByText('The text has been erased.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Restore comment' })).toBeNull()
  })

  it('shows the conflict-of-interest message', async () => {
    review(makeCommentReportDetail(), {
      'POST /admin/comment-reports/cc-1/decision': {
        status: 403,
        body: { detail: 'x', code: 'conflict_of_interest' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/comment-reports/cc-1')
    await user.type(
      await screen.findByRole('textbox', { name: /Note for the record/ }),
      'Keeping it up.',
    )
    await user.click(screen.getByRole('button', { name: 'Keep comment' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Another admin needs to review this one: you’re part of this deed.',
    )
  })
})
