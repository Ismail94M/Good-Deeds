/** F16: My points (M4-DESIGN §6.3, §3.3 #2–#3, §3.4 items 5–7). */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { AHMED, makeEntry, makeMyScoring, makeOptions, PRIYA } from './testFixtures'

const ME = makeMe({ handle: PRIYA.handle, display_name: PRIYA.display_name })

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /scoring/me': { body: makeMyScoring() },
    'GET /scoring/options': { body: makeOptions() },
    'GET /scoring/me/ledger': { body: { items: [], next_cursor: null } },
    ...handlers,
  })
}

describe('MyPointsPage', () => {
  it('shows the server total, provisional points, the split and today’s reporter cap', async () => {
    setup()
    renderApp('/me/points')
    expect(await screen.findByRole('heading', { name: 'My points' })).toBeVisible()
    const summary = await screen.findByRole('region', { name: 'Your points' })
    // total_points from the server (124), never doer + reporter summed here (100 + 14).
    expect(summary).toHaveTextContent('124 points')
    expect(summary).not.toHaveTextContent('114')
    expect(summary).toHaveTextContent('12 still provisional')
    expect(summary).toHaveTextContent('From deeds you did100')
    expect(summary).toHaveTextContent('From deeds you reported14')
    expect(summary).toHaveTextContent('Reports earning points today3 of 10')
    expect(within(summary).getByText('Sprout')).toHaveTextContent('Tier: Sprout')
    expect(
      await screen.findByText(/Points become final 24 hours after a deed is verified\./),
    ).toBeVisible()
  })

  it('lists my boards with rank or "not ranked yet", provisional or final, linking each board', async () => {
    setup({
      'GET /scoring/me': {
        body: makeMyScoring({
          boards: [
            makeMyScoring().boards[0],
            { ...makeMyScoring().boards[1], period_month: '2026-10', rank: 31, final: true },
            { ...makeMyScoring().boards[1], rank: null },
          ],
        }),
      },
    })
    renderApp('/me/points')
    const boards = await screen.findByRole('region', { name: 'Your leaderboards' })
    const links = within(boards).getAllByRole('link')
    expect(links[0]).toHaveTextContent('#4 in Koramangala')
    expect(links[0]).toHaveTextContent('November 2026 · 6 deeds · Provisional')
    expect(links[0]).toHaveAttribute('href', '/leaderboard/W150?month=2026-11')
    expect(links[1]).toHaveTextContent('#31 in Bangalore')
    expect(links[1]).toHaveTextContent('October 2026 · 6 deeds · Final')
    expect(links[1]).toHaveAttribute('href', '/leaderboard/IN-KA-BLR?month=2026-10')
    expect(links[2]).toHaveTextContent('Not ranked yet in Bangalore')
  })

  it('shows history rows with the server’s reason text, category and names, never the description', async () => {
    setup({
      'GET /scoring/me/ledger': {
        body: {
          items: [
            makeEntry(),
            makeEntry({
              id: 'e2',
              kind: 'reversal',
              points: -10,
              reason: 'withdrawn',
              reason_label: 'Report withdrawn',
              provisional: false,
              provisional_until: null,
            }),
            makeEntry({
              id: 'e3',
              kind: 'decision',
              points: 0,
              reason: 'doer_daily_cap',
              reason_label: 'Daily limit reached: still counts on your profile',
              provisional: false,
              provisional_until: null,
            }),
            makeEntry({
              id: 'e4',
              deed_id: 'd9',
              role: 'reporter',
              points: 2,
              doer: AHMED,
              reporter: PRIYA,
              provisional: false,
              provisional_until: null,
            }),
          ],
          next_cursor: null,
        },
      },
    })
    renderApp('/me/points')
    const history = await screen.findByRole('tabpanel')
    const rows = await within(history).findAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('+10Deed verified')
    expect(rows[0]).toHaveTextContent(
      'Medical / emergency help · recognised by Ahmed · provisional until 3 Dec, 14:05',
    )
    expect(rows[1]).toHaveTextContent('−10Report withdrawn')
    expect(rows[1]).not.toHaveTextContent('provisional')
    expect(rows[2]).toHaveTextContent('0Daily limit reached: still counts on your profile')
    expect(rows[3]).toHaveTextContent('+2Deed verified')
    expect(rows[3]).toHaveTextContent('You recognised Ahmed')
    expect(within(rows[3]).getByRole('link', { name: 'View deed' })).toHaveAttribute(
      'href',
      '/me/deeds/d9',
    )
  })

  it('pages the history with the cursor and filters by role', async () => {
    const api = setup({
      'GET /scoring/me/ledger': (req) => {
        if (req.query.get('role') === 'reporter') {
          return { body: { items: [makeEntry({ id: 'r1', role: 'reporter' })], next_cursor: null } }
        }
        return req.query.get('cursor') === 'c2'
          ? { body: { items: [makeEntry({ id: 'e2', points: 8 })], next_cursor: null } }
          : { body: { items: [makeEntry()], next_cursor: 'c2' } }
      },
    })
    renderApp('/me/points')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Show more' }))
    await waitFor(() =>
      expect(within(screen.getByRole('tabpanel')).getAllByRole('listitem')).toHaveLength(2),
    )
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
    expect(api.calls('GET /scoring/me/ledger')[1].query.get('cursor')).toBe('c2')

    await user.click(screen.getByRole('tab', { name: 'My reports' }))
    await waitFor(() =>
      expect(
        api.calls('GET /scoring/me/ledger').some((c) => c.query.get('role') === 'reporter'),
      ).toBe(true),
    )
    await waitFor(() =>
      expect(within(screen.getByRole('tabpanel')).getAllByRole('listitem')).toHaveLength(1),
    )
  })

  it('shows empty states for boards and history', async () => {
    setup({ 'GET /scoring/me': { body: makeMyScoring({ boards: [], provisional_points: 0 }) } })
    renderApp('/me/points')
    expect(await screen.findByText(/You’re not on a leaderboard yet\./)).toBeVisible()
    expect(await screen.findByText(/No points yet\./)).toBeVisible()
    expect(screen.getByRole('region', { name: 'Your points' })).not.toHaveTextContent('provisional')
  })

  it('shows an error with Try again when my points can’t load', async () => {
    setup({
      'GET /scoring/me': [
        // A 4xx isn't retried automatically (5xx are, with back-off).
        {
          status: 429,
          body: { detail: 'Too many requests. Try again soon.', code: 'rate_limited' },
        },
        { body: makeMyScoring() },
      ],
    })
    renderApp('/me/points')
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many requests. Try again soon.')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'Your points' })).toHaveTextContent(
      '124 points',
    )
  })
})
