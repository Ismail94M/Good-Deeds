/**
 * F24, F25 (admin): appeals queue and review (D100: Uphold / Overturn with a note, "This answer
 * is final.") and the audit log (D96: filters, newest first, expandable values, Load more).
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import {
  ADMIN,
  AHMED,
  hoursFromNow,
  makeAppealDetail,
  makeAppealRow,
  makeAuditItem,
  makeFraudOptions,
} from './fraudTestFixtures'

function base(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(makeMe({ role: 'admin', handle: ADMIN.handle, display_name: ADMIN.display_name })),
    'GET /admin/fraud/options': { body: makeFraudOptions() },
    'GET /admin/fraud/status': {
      body: {
        config_version: 1,
        detectors: [],
        open_cases_by_type: {},
        appeals_waiting_for_second_admin: 0,
      },
    },
    ...extra,
  })
}

const NOTE = 'Their reply explains the pattern.'

describe('appeals queue', () => {
  it('lists open appeals with counts and links', async () => {
    base({
      'GET /admin/fraud/appeals': {
        body: {
          items: [makeAppealRow(), makeAppealRow({ appeal_id: 'appeal-2', kind: 'override' })],
          counts: { open: 2, decided: 5 },
          next_cursor: null,
        },
      },
    })
    renderApp('/admin/appeals')
    expect(await screen.findByRole('tab', { name: 'Open · 2' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Decided · 5' })).toBeInTheDocument()
    const rows = screen.getAllByRole('listitem').filter((li) => li.closest('main'))
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Case decision')
    expect(rows[0]).toHaveTextContent('Ahmed Khan')
    expect(rows[0]).toHaveTextContent('2 deeds · original decision by Bala Admin')
    expect(rows[1]).toHaveTextContent('Points change')
    expect(within(rows[0]).getByRole('link', { name: /Review/ })).toHaveAttribute(
      'href',
      '/admin/appeals/appeal-1',
    )
  })

  it('says when appeals need a second admin, and shows the empty state', async () => {
    base({
      'GET /admin/fraud/status': {
        body: {
          config_version: 1,
          detectors: [],
          open_cases_by_type: {},
          appeals_waiting_for_second_admin: 2,
        },
      },
      'GET /admin/fraud/appeals': {
        body: { items: [], counts: { open: 0, decided: 0 }, next_cursor: null },
      },
    })
    renderApp('/admin/appeals')
    expect(await screen.findByText('No open appeals.')).toBeInTheDocument()
    expect(await screen.findByText(/Appeals need a second admin · 2/)).toBeInTheDocument()
  })
})

describe('appeal review', () => {
  it('shows the reason, the notice items and the case link', async () => {
    base({ 'GET /admin/fraud/appeals/appeal-1': { body: makeAppealDetail() } })
    renderApp('/admin/appeals/appeal-1')
    expect(
      await screen.findByRole('heading', { name: 'Appeal from Ahmed Khan' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Their reason' })).toHaveTextContent(
      'Priya and I volunteer together every weekend.',
    )
    const told = screen.getByRole('region', { name: 'What they were told' })
    expect(told).toHaveTextContent('independent witnesses')
    expect(told).toHaveTextContent('Not counted for leaderboards')
    expect(told).toHaveTextContent('Will be removed')
    expect(screen.getByRole('link', { name: 'Open the case' })).toHaveAttribute(
      'href',
      '/admin/fraud/case-1',
    )
  })

  it('overturns with a note; the answer is final', async () => {
    const api = base({
      'GET /admin/fraud/appeals/appeal-1': { body: makeAppealDetail() },
      'POST /admin/fraud/appeals/appeal-1/decision': {
        body: makeAppealDetail({
          outcome: 'overturned',
          reviewer: ADMIN,
          reviewer_note: NOTE,
          decided_at: hoursFromNow(0),
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/appeals/appeal-1')
    await user.click(await screen.findByRole('button', { name: 'Overturn' }))
    const dialog = screen.getByRole('dialog', { name: 'Overturn the decision?' })
    expect(dialog).toHaveTextContent('This answer is final.')
    await user.click(within(dialog).getByRole('button', { name: 'Overturn' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'Write a note of at least 10 characters.',
    )
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Overturn' }))
    await waitFor(() =>
      expect(api.calls('POST /admin/fraud/appeals/appeal-1/decision')[0]?.body).toEqual({
        outcome: 'overturned',
        note: NOTE,
      }),
    )
    expect(await screen.findByText('Appeal overturned.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Uphold' })).toBeNull()
  })

  it('upholds, and reloads when another admin already answered', async () => {
    const api = base({
      'GET /admin/fraud/appeals/appeal-1': { body: makeAppealDetail() },
      'POST /admin/fraud/appeals/appeal-1/decision': {
        status: 409,
        body: { detail: 'raw', code: 'appeal_decided' },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/appeals/appeal-1')
    await user.click(await screen.findByRole('button', { name: 'Uphold' }))
    const dialog = screen.getByRole('dialog', { name: 'Uphold the decision?' })
    expect(dialog).toHaveTextContent('This answer is final.')
    await user.type(within(dialog).getByRole('textbox'), NOTE)
    await user.click(within(dialog).getByRole('button', { name: 'Uphold' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'This appeal has already been decided. The page has been reloaded.',
    )
    expect(api.calls('POST /admin/fraud/appeals/appeal-1/decision')[0]?.body).toEqual({
      outcome: 'upheld',
      note: NOTE,
    })
    await waitFor(() => expect(api.calls('GET /admin/fraud/appeals/appeal-1').length).toBe(2))
  })

  it('a 404 (the decider, or conflicted) shows NotFound', async () => {
    base({
      'GET /admin/fraud/appeals/appeal-1': {
        status: 404,
        body: { detail: 'Appeal not found.', code: 'appeal_not_found' },
      },
    })
    renderApp('/admin/appeals/appeal-1')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})

describe('audit log', () => {
  it('lists entries, expands values and loads more', async () => {
    const api = base({
      'GET /admin/audit': (req) =>
        req.query.get('cursor') === 'c2'
          ? {
              body: {
                items: [makeAuditItem({ id: 'audit-2', action: 'flag_case.park', actor: null })],
                next_cursor: null,
              },
            }
          : { body: { items: [makeAuditItem()], next_cursor: 'c2' } },
    })
    const user = userEvent.setup()
    renderApp('/admin/audit')
    expect(await screen.findByText('flag_case.confirmed')).toBeInTheDocument()
    expect(screen.getByText('They admitted swapping recognitions.')).toBeInTheDocument()

    const show = screen.getByRole('button', { name: 'Show values' })
    expect(show).toHaveAttribute('aria-expanded', 'false')
    await user.click(show)
    expect(screen.getByRole('button', { name: 'Hide values' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(screen.getByText(/"outcome": "confirmed"/)).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByText('flag_case.park')).toBeInTheDocument()
    expect(screen.getByText('System')).toBeInTheDocument()
    expect(api.calls('GET /admin/audit').at(-1)?.query.get('cursor')).toBe('c2')
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
  })

  it('sends the filters to the API, with dates as whole IST days', async () => {
    const api = base({
      'GET /admin/audit': { body: { items: [], next_cursor: null } },
    })
    const user = userEvent.setup()
    renderApp('/admin/audit')
    expect(await screen.findByText('No entries match these filters.')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Entity type'), 'flag_case')
    await user.type(screen.getByLabelText('Actor handle'), `@${AHMED.handle}`)
    await user.type(screen.getByLabelText('Action'), 'flag_case.confirmed')
    await user.type(screen.getByLabelText('From'), '2026-11-01')
    await user.type(screen.getByLabelText('To'), '2026-11-20')
    await user.click(screen.getByRole('button', { name: 'Apply filters' }))
    await waitFor(() => {
      const query = api.calls('GET /admin/audit').at(-1)?.query
      expect(query?.get('entity_type')).toBe('flag_case')
      expect(query?.get('actor')).toBe('ahmedk')
      expect(query?.get('action')).toBe('flag_case.confirmed')
      expect(query?.get('from')).toBe('2026-11-01T00:00:00+05:30')
      expect(query?.get('to')).toBe('2026-11-20T23:59:59.999+05:30')
      expect(query?.has('entity_id')).toBe(false)
    })

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    await waitFor(() => expect(api.calls('GET /admin/audit').at(-1)?.query.toString()).toBe(''))
    expect(screen.getByLabelText('Entity type')).toHaveValue('')
  })

  it('shows the API’s message for a bad date range', async () => {
    base({
      'GET /admin/audit': {
        status: 400,
        body: {
          detail: 'Choose a date range of at most 366 days, starting before it ends.',
          code: 'invalid_range',
        },
      },
    })
    renderApp('/admin/audit?from=2026-12-01&to=2026-11-01')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Choose a date range of at most 366 days, starting before it ends.',
    )
  })
})
