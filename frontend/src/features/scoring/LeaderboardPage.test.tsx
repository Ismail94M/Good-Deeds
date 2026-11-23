/** F17: leaderboards (M4-DESIGN §6.3, §3.3 #6–#8, D67 shared ranks, D69 no points). */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import {
  ASHA,
  makeLeaderboard,
  makeMyScoring,
  makeOptions,
  makeRow,
  PRIYA,
  RAVI,
} from './testFixtures'

const ME = makeMe({ handle: PRIYA.handle, display_name: PRIYA.display_name })
const KORA = { code: 'W150', name: 'Koramangala', kind: 'ward' } as const
const NO_HEROES = { region: KORA, items: [] }

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /leaderboards': { body: makeLeaderboard({ region: KORA }) },
    'GET /leaderboards/heroes': { body: NO_HEROES },
    'GET /scoring/me': { body: makeMyScoring() },
    'GET /scoring/options': { body: makeOptions() },
    ...handlers,
  })
}

const ranking = async () => screen.findByRole('list', { name: 'Ranking' })

describe('LeaderboardPage', () => {
  it('opens my area board from /leaderboard: banner with the date, dense shared ranks, tier and deeds', async () => {
    const api = setup()
    const { router } = renderApp('/leaderboard')
    expect(await screen.findByRole('heading', { level: 1, name: 'Koramangala' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/leaderboard/W150')
    expect(screen.getByText('Area leaderboard · November 2026')).toBeVisible()
    expect(
      screen.getByText('Provisional until 2 Dec, 01:00 IST.').closest('[role="status"]'),
    ).toBeVisible()
    const rows = within(await ranking()).getAllByTestId('board-row')
    expect(rows).toHaveLength(4)
    expect(within(rows[0]).getByLabelText('Rank 1')).toHaveTextContent('1')
    expect(within(rows[1]).getByLabelText('Rank 2, shared')).toHaveTextContent('=2')
    expect(within(rows[2]).getByLabelText('Rank 2, shared')).toHaveTextContent('=2')
    // Dense ranking (Q58): the place after a shared 2 is 3.
    expect(within(rows[3]).getByLabelText('Rank 3')).toHaveTextContent('3')
    expect(rows[0]).toHaveTextContent('Asha Rao')
    expect(rows[0]).toHaveTextContent('Tier: Sprout')
    expect(rows[0]).toHaveTextContent('9 deeds')
    expect(rows[3]).toHaveTextContent('1 deed')
    expect(within(rows[0]).getByRole('link', { name: 'Asha Rao' })).toHaveAttribute(
      'href',
      '/u/asha',
    )
    // My own area board; no month sent.
    expect([...api.calls('GET /leaderboards')[0].query.entries()]).toEqual([['region', 'W150']])
    // Provisional boards show no Heroes.
    expect(screen.queryByRole('heading', { name: 'Kindness Heroes' })).not.toBeInTheDocument()
  })

  it('pins my own row and marks it', async () => {
    setup()
    renderApp('/leaderboard')
    const mine = await screen.findByRole('region', { name: 'Your place' })
    expect(mine).toHaveTextContent('=2')
    expect(mine).toHaveTextContent('Priya Sharma (you)')
    const rows = within(await ranking()).getAllByTestId('board-row')
    expect(rows[2]).toHaveTextContent('(you)')
    expect(rows[1]).not.toHaveTextContent('(you)')
  })

  it('never shows points, even if a reply carried them by mistake (D69)', async () => {
    const leaky = {
      ...makeLeaderboard(),
      items: makeLeaderboard().items.map((row, i) => ({ ...row, points: 9100 + i, score: 777 })),
      me: { ...makeLeaderboard().me, points: 9999 },
    }
    setup({ 'GET /leaderboards': { body: leaky } })
    renderApp('/leaderboard')
    await ranking()
    const main = screen.getByRole('main')
    expect(main).not.toHaveTextContent(/91\d\d|9999|777/)
    expect(within(main).queryByText(/\bpoints?\b/i)).not.toBeInTheDocument()
    // My own points are never fetched for the board screen.
    expect(screen.queryByText('124')).not.toBeInTheDocument()
  })

  it('says "Provisional." without a date when the API sends none', async () => {
    setup({ 'GET /leaderboards': { body: makeLeaderboard({ finalizes_at: undefined }) } })
    renderApp('/leaderboard')
    expect(await screen.findByText('Provisional.')).toBeVisible()
    expect(screen.queryByText(/Provisional until/)).not.toBeInTheDocument()
  })

  it('shows a final ward board with its Heroes, level and place, and marks title holders', async () => {
    const api = setup({
      'GET /leaderboards': {
        body: makeLeaderboard({
          region: { code: 'W150', name: 'Koramangala', kind: 'ward' },
          month: '2026-10',
          final: true,
          finalizes_at: null,
          items: [makeRow(), makeRow({ rank: 2, user: RAVI })],
          me: null,
          titles: [
            {
              place: 1,
              label: 'Kindness Hero · Koramangala · October 2026',
              users: [ASHA],
            },
          ],
        }),
      },
    })
    renderApp('/leaderboard/W150?month=2026-10')
    expect(await screen.findByRole('heading', { level: 1, name: 'Koramangala' })).toBeVisible()
    expect(screen.getByText('Area leaderboard · October 2026')).toBeVisible()
    expect(screen.getByText('Final.')).toBeVisible()
    expect(screen.queryByText(/Provisional/)).not.toBeInTheDocument()
    const heroes = screen.getByRole('region', { name: 'Kindness Heroes' })
    expect(heroes).toHaveTextContent('#1 · Kindness Hero · Koramangala · October 2026')
    expect(within(heroes).getByRole('link', { name: 'Asha Rao' })).toHaveAttribute(
      'href',
      '/u/asha',
    )
    const rows = within(await ranking()).getAllByTestId('board-row')
    expect(rows[0]).toHaveTextContent('Kindness Hero · Koramangala · October 2026')
    expect(rows[1]).not.toHaveTextContent('Kindness Hero')
    expect(screen.queryByRole('region', { name: 'Your place' })).not.toBeInTheDocument()
    const call = api.calls('GET /leaderboards')[0]
    expect(call.query.get('region')).toBe('W150')
    expect(call.query.get('month')).toBe('2026-10')
  })

  it('explains a final ward board with no Kindness Hero', async () => {
    setup({
      'GET /leaderboards': {
        body: makeLeaderboard({
          region: { code: 'W150', name: 'Koramangala', kind: 'ward' },
          final: true,
          titles: [],
        }),
      },
    })
    renderApp('/leaderboard/W150')
    expect(await screen.findByText(/^No Kindness Hero this month/)).toBeVisible()
  })

  it('shows an account no longer active as "Unavailable account", unlinked, keeping its place (SEC3 L6)', async () => {
    const gone = { handle: null, display_name: 'Unavailable account' }
    setup({
      'GET /leaderboards': {
        body: makeLeaderboard({
          region: { code: 'W150', name: 'Koramangala', kind: 'ward' },
          month: '2026-10',
          final: true,
          finalizes_at: null,
          items: [makeRow({ user: gone }), makeRow({ rank: 2, user: RAVI })],
          me: null,
          titles: [
            { place: 1, label: 'Kindness Hero · Koramangala · October 2026', users: [gone] },
          ],
        }),
      },
    })
    renderApp('/leaderboard/W150?month=2026-10')
    const heroes = await screen.findByRole('region', { name: 'Kindness Heroes' })
    expect(heroes).toHaveTextContent('Unavailable account')
    expect(within(heroes).queryByRole('link')).not.toBeInTheDocument()
    const rows = within(await ranking()).getAllByTestId('board-row')
    expect(rows[0]).toHaveTextContent('Unavailable account')
    expect(within(rows[0]).getByLabelText('Rank 1')).toBeVisible()
    expect(within(rows[0]).queryByRole('link')).not.toBeInTheDocument()
    expect(within(rows[1]).getByRole('link', { name: 'Ravi Kumar' })).toBeVisible()
  })

  it('switches months with the picker and keeps it in the URL', async () => {
    const api = setup()
    const { router } = renderApp('/leaderboard')
    const select = await screen.findByRole('combobox', { name: 'Month' })
    const options = within(select).getAllByRole('option') as HTMLOptionElement[]
    expect(options.length).toBeGreaterThanOrEqual(12)
    const past = options[1].value
    await userEvent.setup().selectOptions(select, past)
    await waitFor(() => expect(router.state.location.search).toBe(`?month=${past}`))
    await waitFor(() =>
      expect(api.calls('GET /leaderboards').some((c) => c.query.get('month') === past)).toBe(true),
    )
  })

  it('finds an area by name and links to its board; lists my areas only', async () => {
    const api = setup({
      'GET /leaderboards/regions': { body: [{ code: 'W151', name: 'Koramangala East' }] },
    })
    renderApp('/leaderboard/W150')
    const boards = await screen.findByRole('navigation', { name: 'Your leaderboards' })
    expect(await within(boards).findByRole('link', { name: 'Koramangala' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    // My city board (an old snapshot) is never offered while City is locked.
    expect(within(boards).queryByRole('link', { name: 'Bangalore' })).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.type(screen.getByRole('searchbox', { name: 'Find an area' }), 'k')
    expect(screen.getByText('Type at least 2 letters.')).toBeVisible()
    await user.type(screen.getByRole('searchbox', { name: 'Find an area' }), 'ora')
    const wards = await screen.findByRole('list', { name: 'Areas' })
    expect(within(wards).getByRole('link', { name: 'Koramangala East' })).toHaveAttribute(
      'href',
      '/leaderboard/W151',
    )
    const search = api.calls('GET /leaderboards/regions').at(-1)
    expect(search?.query.get('kind')).toBe('ward')
    expect(search?.query.get('q')).toBe('kora')
  })

  it('says when no area matches', async () => {
    setup({ 'GET /leaderboards/regions': { body: [] } })
    renderApp('/leaderboard/W150')
    await userEvent
      .setup()
      .type(await screen.findByRole('searchbox', { name: 'Find an area' }), 'zzz')
    expect(await screen.findByText(/No areas match that name/)).toBeVisible()
  })

  it('shows Area open and City, State and Country locked, from config', async () => {
    setup()
    renderApp('/leaderboard/W150')
    const levels = await screen.findByRole('navigation', { name: 'Leaderboard levels' })
    const area = within(levels).getByRole('link', { name: 'Area' })
    expect(area).toHaveAttribute('href', '/leaderboard')
    await waitFor(() => expect(area).toHaveAttribute('aria-current', 'page'))
    for (const name of ['City', 'State', 'Country']) {
      const locked = within(levels).getByRole('link', { name: `${name}, locked` })
      expect(locked).toHaveAttribute('aria-disabled', 'true')
      expect(locked).not.toHaveAttribute('href')
      expect(locked).toHaveTextContent(`${name}Locked`)
    }
    expect(screen.getByText('City, State and Country leaderboards are coming soon.')).toBeVisible()
  })

  it('opens a level as soon as config enables it, with no UI change', async () => {
    const levels = makeOptions().levels.map((l) =>
      l.kind === 'city' ? { ...l, enabled: true } : l,
    )
    setup({ 'GET /scoring/options': { body: makeOptions({ levels }) } })
    renderApp('/leaderboard/W150?month=2026-10')
    const nav = await screen.findByRole('navigation', { name: 'Leaderboard levels' })
    expect(await within(nav).findByRole('link', { name: 'City' })).toHaveAttribute(
      'href',
      '/leaderboard?level=city&month=2026-10',
    )
    expect(within(nav).getByRole('link', { name: 'State, locked' })).toBeVisible()
    expect(screen.getByText('State and Country leaderboards are coming soon.')).toBeVisible()
  })

  it('shows a locked level as locked and never asks for its board', async () => {
    const api = setup()
    renderApp('/leaderboard?level=city')
    expect(
      await screen.findByRole('heading', { level: 1, name: 'City leaderboards are locked' }),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Area leaderboard' })).toHaveAttribute(
      'href',
      '/leaderboard',
    )
    expect(api.calls('GET /leaderboards')).toHaveLength(0)
  })

  it('asks me to find my area when I am on no area board yet', async () => {
    const api = setup({ 'GET /scoring/me': { body: makeMyScoring({ boards: [] }) } })
    renderApp('/leaderboard')
    expect(await screen.findByRole('heading', { level: 1, name: 'Area leaderboard' })).toBeVisible()
    expect(screen.getByRole('searchbox', { name: 'Find an area' })).toBeVisible()
    expect(api.calls('GET /leaderboards')).toHaveLength(0)
  })

  it('shows past Kindness Heroes for the region', async () => {
    const api = setup({
      'GET /leaderboards/heroes': {
        body: {
          region: { code: 'W150', name: 'Koramangala', kind: 'ward' },
          items: [
            {
              period_month: '2026-10',
              titles: [
                {
                  place: 1,
                  label: 'Kindness Hero · Koramangala · October 2026',
                  users: [ASHA, RAVI],
                },
              ],
            },
          ],
        },
      },
    })
    renderApp('/leaderboard/W150')
    const history = await screen.findByRole('region', { name: 'Past Kindness Heroes' })
    expect(within(history).getByRole('heading', { name: 'October 2026' })).toBeVisible()
    expect(history).toHaveTextContent('#1 · Kindness Hero · Koramangala · October 2026')
    expect(history).toHaveTextContent('Asha Rao, Ravi Kumar')
    expect(api.calls('GET /leaderboards/heroes')[0].query.get('region')).toBe('W150')
  })

  it('shows an empty board', async () => {
    setup({ 'GET /leaderboards': { body: makeLeaderboard({ items: [], me: null }) } })
    renderApp('/leaderboard')
    expect(await screen.findByText(/No one is on this board yet\./)).toBeVisible()
    expect(await screen.findByText(/No Heroes yet\./)).toBeVisible()
  })

  it('shows "Board not found" for an unknown region', async () => {
    setup({
      'GET /leaderboards': { status: 404, body: { detail: 'Not found.', code: 'not_found' } },
    })
    renderApp('/leaderboard/NOPE')
    expect(await screen.findByRole('heading', { name: 'Board not found' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Area leaderboard' })).toHaveAttribute(
      'href',
      '/leaderboard',
    )
  })

  it('shows the API message for a bad month', async () => {
    setup({
      'GET /leaderboards': {
        status: 400,
        body: { detail: 'Pick a month that has started.', code: 'invalid_month' },
      },
    })
    renderApp('/leaderboard?month=2030-01')
    expect(await screen.findByRole('alert')).toHaveTextContent('Pick a month that has started.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
  })
})
