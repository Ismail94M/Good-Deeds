/** Featured City Top Performers (D72): podium + list under the Area search. */

import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { FeaturedCityBoard, FeaturedRow } from './scoringApi'
import {
  AHMED,
  ASHA,
  makeLeaderboard,
  makeMyScoring,
  makeOptions,
  PRIYA,
  RAVI,
  SEED,
  SPROUT,
} from './testFixtures'

const ME = makeMe({ handle: PRIYA.handle, display_name: PRIYA.display_name })
const KORA = { code: 'W150', name: 'Koramangala', kind: 'ward' } as const
const BLR = { key: 'bengaluru', name: 'Bengaluru' }
const MEERA = { handle: 'meera', display_name: 'Meera Iyer' }

function person(overrides: Partial<FeaturedRow>): FeaturedRow {
  return {
    rank: 1,
    shared: false,
    user: ASHA,
    tier: SPROUT,
    deed_count: 9,
    points: 92,
    ...overrides,
  }
}

function featured(overrides: Partial<FeaturedCityBoard> = {}): FeaturedCityBoard {
  return {
    city: BLR,
    cities: [BLR],
    month: '2026-11',
    final: false,
    items: [
      person({}),
      person({ rank: 2, user: RAVI, tier: SEED, deed_count: 6, points: 61 }),
      person({ rank: 3, user: PRIYA, tier: SEED, deed_count: 5, points: 48 }),
      person({ rank: 4, shared: true, user: AHMED, tier: SEED, deed_count: 3, points: 30 }),
      person({ rank: 4, shared: true, user: MEERA, tier: SEED, deed_count: 3, points: 30 }),
    ],
    ...overrides,
  }
}

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /leaderboards': { body: makeLeaderboard({ region: KORA }) },
    'GET /leaderboards/heroes': { body: { region: KORA, items: [] } },
    'GET /leaderboards/featured': { body: featured() },
    'GET /scoring/me': { body: makeMyScoring() },
    'GET /scoring/options': { body: makeOptions() },
    ...handlers,
  })
}

async function section() {
  const name = 'Featured City Top Performers'
  await screen.findByRole('region', { name })
  await waitFor(() => expect(screen.queryByText('Loading top performers…')).not.toBeInTheDocument())
  // The loading box is replaced by the loaded one, so look it up again.
  return screen.getByRole('region', { name })
}

describe('FeaturedCity', () => {
  it('shows Bengaluru’s top 5 under the area search: podium, then the list', async () => {
    const api = setup()
    renderApp('/leaderboard/W150')
    const box = await section()
    expect(box).toHaveTextContent('Bengaluru · November 2026 · Provisional')
    // It sits below the area search and above the Area board.
    const search = screen.getByRole('searchbox', { name: 'Find an area' })
    expect(search.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const board = await screen.findByRole('heading', { level: 1, name: 'Koramangala' })
    expect(box.compareDocumentPosition(board) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    const podium = within(box).getByRole('list', { name: 'Top performers in Bengaluru' })
    const places = within(podium).getAllByTestId('podium-place')
    expect(places).toHaveLength(3)
    // Rank order in the DOM (screen readers); the 2 · 1 · 3 layout is visual only.
    expect(within(places[0]).getByLabelText('Rank 1')).toHaveTextContent('1')
    expect(places[0]).toHaveTextContent('Asha Rao')
    expect(places[0]).toHaveTextContent('92 points')
    expect(places[0]).toHaveTextContent('9 deeds')
    expect(places[0]).toHaveTextContent('Gold')
    expect(places[0]).toHaveClass('order-2')
    expect(places[1]).toHaveTextContent('Ravi Kumar')
    expect(places[1]).toHaveClass('order-1')
    expect(places[2]).toHaveTextContent('Priya Sharma')
    expect(within(places[0]).getByRole('link', { name: 'Asha Rao' })).toHaveAttribute(
      'href',
      '/u/asha',
    )

    const more = within(box).getByRole('list', { name: 'More top performers in Bengaluru' })
    const rows = within(more).getAllByTestId('featured-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByLabelText('Rank 4, shared')).toHaveTextContent('=4')
    expect(rows[1]).toHaveTextContent('Meera Iyer')
    expect(rows[1]).toHaveTextContent('30 points')
    expect(rows[1]).toHaveTextContent('3 deeds')

    // The default featured city and the current month: nothing chosen on the client.
    expect([...api.calls('GET /leaderboards/featured')[0].query.keys()]).toEqual([])
    // The Area board itself still never shows points (D69).
    const ranking = screen.getByRole('list', { name: 'Ranking' })
    expect(ranking).not.toHaveTextContent(/points?/i)
  })

  it('follows the month picked for the page and says Final once final', async () => {
    const api = setup({
      'GET /leaderboards/featured': { body: featured({ month: '2026-10', final: true }) },
    })
    renderApp('/leaderboard/W150?month=2026-10')
    expect(await section()).toHaveTextContent('Bengaluru · October 2026 · Final')
    expect(api.calls('GET /leaderboards/featured')[0].query.get('month')).toBe('2026-10')
  })

  it('hides points when the city’s config does', async () => {
    const items = featured().items.map((row) => ({ ...row, points: null }))
    setup({ 'GET /leaderboards/featured': { body: featured({ items }) } })
    renderApp('/leaderboard/W150')
    const box = await section()
    expect(box).toHaveTextContent('9 deeds')
    expect(
      within(box).getByRole('list', { name: 'Top performers in Bengaluru' }),
    ).not.toHaveTextContent(/points?/i)
  })

  it('shows a podium with fewer than 3 people and no list', async () => {
    setup({ 'GET /leaderboards/featured': { body: featured({ items: [person({})] }) } })
    renderApp('/leaderboard/W150')
    const box = await section()
    expect(within(box).getAllByTestId('podium-place')).toHaveLength(1)
    expect(within(box).queryByRole('list', { name: /More top performers/ })).not.toBeInTheDocument()
  })

  it('says when nobody from the city is ranked yet', async () => {
    setup({ 'GET /leaderboards/featured': { body: featured({ items: [] }) } })
    renderApp('/leaderboard/W150')
    expect(await section()).toHaveTextContent('No one from Bengaluru is ranked yet this month.')
  })

  it('also shows on the area search page when I have no area board', async () => {
    setup({ 'GET /scoring/me': { body: makeMyScoring({ boards: [] }) } })
    renderApp('/leaderboard')
    expect(await section()).toHaveTextContent('Bengaluru')
    expect(screen.getByRole('heading', { level: 1, name: 'Area leaderboard' })).toBeVisible()
  })

  it('is left out when no city is featured (404), and the Area board still works', async () => {
    setup({
      'GET /leaderboards/featured': {
        status: 404,
        body: { detail: 'Not found.', code: 'board_not_found' },
      },
    })
    renderApp('/leaderboard/W150')
    expect(await screen.findByRole('heading', { level: 1, name: 'Koramangala' })).toBeVisible()
    expect(
      screen.queryByRole('region', { name: 'Featured City Top Performers' }),
    ).not.toBeInTheDocument()
  })

  it('keeps City, State and Country locked', async () => {
    setup()
    renderApp('/leaderboard/W150')
    await section()
    const levels = screen.getByRole('navigation', { name: 'Leaderboard levels' })
    for (const name of ['City', 'State', 'Country']) {
      expect(within(levels).getByRole('link', { name: `${name}, locked` })).toHaveAttribute(
        'aria-disabled',
        'true',
      )
    }
  })
})
