/**
 * F20, F21, F25 (admin): fraud cases queue and case review (docs/M5-DESIGN.md §6.2, D88, D90,
 * D99): tabs and type chips, the deadline chip, the review sections, Park / Ask / Dismiss /
 * Confirm, Confirm gating, the per-deed Release default, error copy, NotFound and the admin nav.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { QUEUE_NOTE } from './FraudCasesPage'
import type { CaseDetail } from './fraudApi'
import { formatShortDate } from './fraudFormat'
import {
  ADMIN,
  AHMED,
  answeredExplanations,
  hoursFromNow,
  makeCaseDeed,
  makeCaseDetail,
  makeCasePage,
  makeCaseRow,
  makeFraudOptions,
  PRIYA,
} from './fraudTestFixtures'

function base(extra: Parameters<typeof mockApi>[0] = {}, role: 'admin' | 'moderator' = 'admin') {
  return mockApi({
    ...signedIn(makeMe({ role, handle: ADMIN.handle, display_name: ADMIN.display_name })),
    'GET /admin/fraud/options': { body: makeFraudOptions() },
    ...extra,
  })
}

const NOTE = 'Checked both profiles carefully.'

describe('fraud cases queue', () => {
  it('shows tabs with counts, type chips, the note and rows', async () => {
    base({
      'GET /admin/fraud/cases': {
        body: makeCasePage([
          makeCaseRow({ review_by: hoursFromNow(20) }),
          makeCaseRow({
            case_id: 'case-2',
            type: 'ring',
            users: [AHMED, PRIYA, { handle: 'ravi', display_name: 'Ravi Kumar' }],
            user_count: 5,
            deed_count: 6,
            held_count: 2,
            summary: 'Ahmed, Priya and Ravi recognised each other in a ring (6 deeds)',
          }),
        ]),
      },
    })
    renderApp('/admin/fraud')
    expect(await screen.findByRole('heading', { name: 'Fraud cases' })).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: 'Open · 2' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Parked · 1' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Resolved · 4' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'A case is a pattern worth a look, not proof. Nothing changes for anyone until you decide. Decide before the month’s boards become final if you can.',
      ),
    ).toBeInTheDocument()
    expect(QUEUE_NOTE).toContain('not proof')

    const chips = screen.getByRole('group', { name: 'Case type' })
    expect(within(chips).getByRole('button', { name: 'All types' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(chips).getByRole('button', { name: 'Content' })).toBeInTheDocument()
    expect(within(chips).queryByRole('button', { name: 'Invite anomaly' })).toBeNull()

    const rows = screen.getAllByRole('listitem').filter((li) => li.closest('main'))
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent(
      'Ahmed Khan and Priya Sharma recognised each other 3 times in 12 days',
    )
    expect(rows[0]).toHaveTextContent('Ahmed Khan and Priya Sharma')
    expect(rows[0]).toHaveTextContent('3 deeds · 0 held')
    expect(rows[0]).toHaveTextContent('Opened 5 h ago')
    expect(rows[1]).toHaveTextContent('Ahmed Khan, Priya Sharma, Ravi Kumar +2')
    expect(rows[1]).toHaveTextContent('6 deeds · 2 held')
    expect(rows[1]).toHaveTextContent('Ring')
    expect(within(rows[0]).getByRole('link', { name: /Review/ })).toHaveAttribute(
      'href',
      '/admin/fraud/case-1',
    )
  })

  it('makes the "Board final" chip red within 48 hours only', async () => {
    base({
      'GET /admin/fraud/cases': {
        body: makeCasePage([
          makeCaseRow({ review_by: hoursFromNow(20) }),
          makeCaseRow({ case_id: 'case-2', review_by: hoursFromNow(24 * 5) }),
        ]),
      },
    })
    renderApp('/admin/fraud')
    await screen.findByRole('tab', { name: 'Open · 2' })
    const rows = screen.getAllByRole('listitem').filter((li) => li.closest('main'))
    const soon = within(rows[0]).getByText(/Board final/)
    const later = within(rows[1]).getByText(/Board final/)
    expect(soon).toHaveClass('bg-red-100')
    expect(soon).toHaveTextContent('(soon)')
    expect(later).not.toHaveClass('bg-red-100')
  })

  it('switches tabs and type filters through the API', async () => {
    const api = base({
      'GET /admin/fraud/cases': (req) => ({
        body: makeCasePage(
          req.query.get('status') === 'parked'
            ? [makeCaseRow({ status: 'parked', case_id: 'parked-1' })]
            : [makeCaseRow()],
        ),
      }),
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud')
    await user.click(await screen.findByRole('tab', { name: 'Parked · 1' }))
    await waitFor(() =>
      expect(api.calls('GET /admin/fraud/cases').at(-1)?.query.get('status')).toBe('parked'),
    )
    await user.click(screen.getByRole('button', { name: 'Ring' }))
    await waitFor(() => {
      const last = api.calls('GET /admin/fraud/cases').at(-1)
      expect(last?.query.get('type')).toBe('ring')
      expect(last?.query.get('status')).toBe('parked')
    })
    expect(screen.getByRole('button', { name: 'Ring' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('shows the empty and error states', async () => {
    base({
      'GET /admin/fraud/cases': { body: makeCasePage([], { open: 0, parked: 0, resolved: 0 }) },
    })
    renderApp('/admin/fraud')
    expect(await screen.findByText('No open cases.')).toBeInTheDocument()
  })

  it('shows an error with Try again', async () => {
    base({
      'GET /admin/fraud/cases': { status: 500, body: { detail: 'Server error.', code: 'x' } },
    })
    renderApp('/admin/fraud')
    expect(await screen.findByRole('alert')).toHaveTextContent('Server error.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('admin nav and roles', () => {
  it('shows the fraud links to admins', async () => {
    base({ 'GET /admin/fraud/cases': { body: makeCasePage([]) } })
    renderApp('/admin/fraud')
    const nav = await screen.findByRole('navigation', { name: 'Admin' })
    expect(within(nav).getByRole('link', { name: 'Fraud cases' })).toHaveAttribute(
      'href',
      '/admin/fraud',
    )
    expect(within(nav).getByRole('link', { name: 'Appeals' })).toHaveAttribute(
      'href',
      '/admin/appeals',
    )
    expect(within(nav).getByRole('link', { name: 'Override requests' })).toHaveAttribute(
      'href',
      '/admin/scoring/override-requests',
    )
    expect(within(nav).getByRole('link', { name: 'Audit log' })).toHaveAttribute(
      'href',
      '/admin/audit',
    )
  })

  it('hides them from moderators, who keep photo and comment reports', async () => {
    base({}, 'moderator')
    renderApp('/admin/fraud')
    const nav = await screen.findByRole('navigation', { name: 'Admin' })
    expect(within(nav).getByRole('link', { name: 'Photo reports' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Comment reports' })).toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Fraud cases' })).toBeNull()
    expect(within(nav).queryByRole('link', { name: 'Appeals' })).toBeNull()
    expect(within(nav).queryByRole('link', { name: 'Audit log' })).toBeNull()
  })

  it.each(['/admin/fraud', '/admin/fraud/case-1', '/admin/appeals', '/admin/audit'])(
    'a moderator opening %s gets NotFound',
    async (path) => {
      const api = base({}, 'moderator')
      renderApp(path)
      expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
      expect(api.requests.some((r) => r.path.startsWith('/admin/fraud/cases'))).toBe(false)
    },
  )
})

function review(detail: CaseDetail, extra: Parameters<typeof mockApi>[0] = {}) {
  return base({ 'GET /admin/fraud/cases/case-1': { body: detail }, ...extra })
}

describe('case review', () => {
  it('shows what was found, people, connections, deeds and links', async () => {
    review(makeCaseDetail())
    renderApp('/admin/fraud/case-1')
    expect(await screen.findByRole('heading', { name: 'Reciprocal case' })).toBeInTheDocument()
    const found = screen.getByRole('region', { name: 'What was found' })
    expect(found).toHaveTextContent('recognised each other 3 times in 12 days')
    expect(found).toHaveTextContent('Recognitions each way: 2 and 1.')

    const people = screen.getByRole('region', { name: 'People (2)' })
    expect(people).toHaveTextContent('Ahmed Khan')
    expect(people).toHaveTextContent('Other open cases1')
    // Open cases don't link to revocation.
    expect(within(people).queryByRole('link', { name: /Revoke/ })).toBeNull()

    const edges = screen.getByRole('region', { name: 'Connections' })
    expect(within(edges).getByText('Same phone used')).toBeInTheDocument()
    expect(within(edges).getByText('Same network')).toBeInTheDocument()

    const deeds = screen.getByRole('region', { name: 'Deeds (2)' })
    const items = within(deeds).getAllByRole('listitem')
    expect(items[0]).not.toHaveTextContent('Independent witness too')
    expect(items[1]).toHaveTextContent('Independent witness too')
    expect(within(items[0]).getByRole('link', { name: /Points and overrides/ })).toHaveAttribute(
      'href',
      '/admin/scoring/deeds/deed-1',
    )
    expect(screen.getByText(/November 2026 board final/)).toBeInTheDocument()
  })

  it('shows each deed fact once: no repeated Rejected and no Held once rejected', async () => {
    review(
      makeCaseDetail({
        deeds: [
          makeCaseDeed({
            deed_id: 'deed-1',
            status: 'rejected',
            counts_for_points: false,
            held: true,
            outcome: 'reject',
          }),
          makeCaseDeed({ deed_id: 'deed-2', held: true, outcome: 'exclude' }),
          makeCaseDeed({ deed_id: 'deed-3', held: true, outcome: 'reject' }),
        ],
      }),
    )
    renderApp('/admin/fraud/case-1')
    const deeds = await screen.findByRole('region', { name: 'Deeds (3)' })
    const [rejected, excluded, pending] = within(deeds).getAllByRole('listitem')
    expect(within(rejected).getAllByText('Rejected')).toHaveLength(1)
    expect(within(rejected).queryByText('Held')).toBeNull()
    expect(within(rejected).getByText('Not counted for points')).toBeInTheDocument()
    // Still verified: the hold and the decision are separate facts.
    expect(within(excluded).getByText('Verified')).toBeInTheDocument()
    expect(within(excluded).getByText('Held')).toBeInTheDocument()
    expect(within(excluded).getByText('Excluded from leaderboards')).toBeInTheDocument()
    // Rejection waiting for the appeal window (D100): verified, held, outcome Rejected.
    expect(within(pending).getByText('Verified')).toBeInTheDocument()
    expect(within(pending).getByText('Held')).toBeInTheDocument()
    expect(within(pending).getAllByText('Rejected')).toHaveLength(1)
  })

  it('a 404 (unknown or conflicted) shows NotFound', async () => {
    base({
      'GET /admin/fraud/cases/case-1': {
        status: 404,
        body: { detail: 'Case not found.', code: 'case_not_found' },
      },
    })
    renderApp('/admin/fraud/case-1')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })

  it('Confirm is disabled until someone is asked', async () => {
    review(makeCaseDetail())
    renderApp('/admin/fraud/case-1')
    const confirm = await screen.findByRole('button', { name: 'Confirm' })
    expect(confirm).toBeDisabled()
    expect(confirm).toHaveAccessibleDescription('Ask the people involved for an explanation first')
  })

  it('Confirm waits for a reply until the due date', async () => {
    const due = hoursFromNow(24 * 7)
    review(
      makeCaseDetail({
        explanations: [
          {
            user: AHMED,
            requested_at: hoursFromNow(-2),
            reply_due_at: due,
            reply: null,
            replied_at: null,
          },
        ],
      }),
    )
    renderApp('/admin/fraud/case-1')
    const confirm = await screen.findByRole('button', { name: 'Confirm' })
    expect(confirm).toBeDisabled()
    expect(confirm).toHaveAccessibleDescription(`Waiting for a reply until ${formatShortDate(due)}`)
    expect(screen.getByRole('region', { name: 'Explanations' })).toHaveTextContent(
      'Waiting for a reply',
    )
  })

  it('confirms with Release by default and sends each deed’s choice', async () => {
    const api = review(makeCaseDetail({ explanations: answeredExplanations() }), {
      'POST /admin/fraud/cases/case-1/decision': {
        body: makeCaseDetail({
          case: { status: 'resolved', outcome: 'confirmed', resolved_at: hoursFromNow(0) },
          explanations: answeredExplanations(),
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    const explanations = await screen.findByRole('region', { name: 'Explanations' })
    expect(explanations).toHaveTextContent('We work together at the shelter.')
    expect(explanations).toHaveTextContent('No reply, due date passed')

    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    const dialog = screen.getByRole('dialog', { name: 'Confirm this case' })
    expect(dialog).toHaveTextContent(
      'People are told the outcome for their own deeds and can ask for another review within 30 days. A rejection only happens after that.',
    )
    const selects = within(dialog).getAllByRole('combobox')
    expect(selects).toHaveLength(2)
    for (const select of selects) expect(select).toHaveValue('release')
    expect(selects[0]).toHaveFocus()
    expect(within(dialog).getByText('Independent witness too')).toBeInTheDocument()

    // The note is required (10 characters in these options).
    await user.click(within(dialog).getByRole('button', { name: 'Confirm case' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'Write a note of at least 10 characters.',
    )
    expect(api.calls('POST /admin/fraud/cases/case-1/decision')).toHaveLength(0)

    await user.selectOptions(selects[0], 'Keep but exclude from leaderboards')
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Confirm case' }))
    await waitFor(() =>
      expect(api.calls('POST /admin/fraud/cases/case-1/decision')[0]?.body).toEqual({
        outcome: 'confirmed',
        note: NOTE,
        deeds: [
          { deed_id: 'deed-1', action: 'exclude' },
          { deed_id: 'deed-2', action: 'release' },
        ],
      }),
    )
    expect(await screen.findByText('Case confirmed.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    // Resolved: read-only, with revocation links per subject.
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
    expect(
      screen.getByRole('link', { name: 'Revoke titles or badges for Ahmed Khan' }),
    ).toHaveAttribute('href', '/admin/users/ahmedk')
  })

  it.each([
    [
      409,
      'explanation_required',
      'Ask the people involved for an explanation first. Everyone whose deeds you exclude or reject needs to be asked.',
    ],
    [
      422,
      'use_dismiss',
      'Every deed is set to Release. Choose another outcome for at least one deed, or dismiss the case instead.',
    ],
    [
      422,
      'deed_actions_invalid',
      'The deeds in this case changed. Reload the page and choose an outcome for each deed again.',
    ],
    [422, 'note_too_long', 'Keep the note to 2000 characters or fewer.'],
  ])('shows friendly copy for %s %s', async (status, code, message) => {
    review(makeCaseDetail({ explanations: answeredExplanations() }), {
      'POST /admin/fraud/cases/case-1/decision': {
        status,
        body: { detail: 'raw', code },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Confirm case' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(message)
  })

  it('explains a pending reply from the API', async () => {
    review(makeCaseDetail({ explanations: answeredExplanations() }), {
      'POST /admin/fraud/cases/case-1/decision': {
        status: 409,
        body: { detail: 'Waiting for a reply until 2026-12-10.', code: 'explanation_pending' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Confirm case' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Waiting for a reply until 2026-12-10. You can confirm once they reply or the date has passed.',
    )
  })

  it('a case decided meanwhile reloads with a message', async () => {
    const api = review(makeCaseDetail(), {
      'POST /admin/fraud/cases/case-1/decision': {
        status: 409,
        body: { detail: 'raw', code: 'case_resolved' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    await user.click(await screen.findByRole('button', { name: 'Dismiss' }))
    const dialog = screen.getByRole('dialog', { name: 'Dismiss this case?' })
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Dismiss case' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'This case has already been decided. The page has been reloaded.',
    )
    await waitFor(() => expect(api.calls('GET /admin/fraud/cases/case-1').length).toBe(2))
  })

  it('dismisses with a note and no deed outcomes', async () => {
    const api = review(makeCaseDetail(), {
      'POST /admin/fraud/cases/case-1/decision': {
        body: makeCaseDetail({
          case: { status: 'resolved', outcome: 'dismissed', resolved_at: hoursFromNow(0) },
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    await user.click(await screen.findByRole('button', { name: 'Dismiss' }))
    const dialog = screen.getByRole('dialog', { name: 'Dismiss this case?' })
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Dismiss case' }))
    await waitFor(() =>
      expect(api.calls('POST /admin/fraud/cases/case-1/decision')[0]?.body).toEqual({
        outcome: 'dismissed',
        note: NOTE,
      }),
    )
    expect(await screen.findByText('Case dismissed.')).toBeInTheDocument()
    // A dismissed case is no basis for revocation.
    expect(screen.queryByRole('link', { name: /Revoke/ })).toBeNull()
  })

  it('parks and unparks with a note; Escape closes and returns focus', async () => {
    const parked = makeCaseDetail({
      case: { status: 'parked' },
      decisions: [{ action: 'park', note: NOTE, actor: ADMIN, created_at: hoursFromNow(0) }],
    })
    const api = review(makeCaseDetail(), {
      'POST /admin/fraud/cases/case-1/park': { body: parked },
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    const park = await screen.findByRole('button', { name: 'Park' })
    await user.click(park)
    expect(screen.getByRole('dialog', { name: 'Park this case?' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(park).toHaveFocus()

    await user.click(park)
    const dialog = screen.getByRole('dialog', { name: 'Park this case?' })
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Park case' }))
    await waitFor(() =>
      expect(api.calls('POST /admin/fraud/cases/case-1/park')[0]?.body).toEqual({ note: NOTE }),
    )
    expect(await screen.findByRole('button', { name: 'Unpark' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Decision history' })).toHaveTextContent(
      `Parked by ${ADMIN.display_name}`,
    )
  })

  it('asks the chosen people for an explanation', async () => {
    const api = review(makeCaseDetail(), {
      'POST /admin/fraud/cases/case-1/explanations': {
        body: makeCaseDetail({
          explanations: [
            {
              user: PRIYA,
              requested_at: hoursFromNow(0),
              reply_due_at: hoursFromNow(24 * 14),
              reply: null,
              replied_at: null,
            },
          ],
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    await user.click(await screen.findByRole('button', { name: 'Ask for an explanation' }))
    const dialog = screen.getByRole('dialog', { name: 'Ask for an explanation' })
    expect(dialog).toHaveTextContent('can reply within 14 days')
    const boxes = within(dialog).getAllByRole('checkbox')
    expect(boxes).toHaveLength(2)
    await user.click(within(dialog).getByRole('checkbox', { name: /Ahmed Khan/ }))
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Send request' }))
    await waitFor(() =>
      expect(api.calls('POST /admin/fraud/cases/case-1/explanations')[0]?.body).toEqual({
        handles: ['priya'],
        note: NOTE,
      }),
    )
    expect(await screen.findByRole('button', { name: 'Confirm' })).toHaveAccessibleDescription(
      /Waiting for a reply until/,
    )
  })
})
