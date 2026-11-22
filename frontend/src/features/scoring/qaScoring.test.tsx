/**
 * QA8: leaderboard gaps (M4-DESIGN §6.3, §3.4 items 10–11, D67/Q58 dense shared ranks, D69 no
 * public points): the "no Hero" copy names the level's minimums from `/scoring/options`,
 * shared #1 title holders, provisional boards never show titles, and leaked points in the
 * heroes reply never render.
 */

import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { noHeroText } from './scoringText'
import {
  AHMED,
  ASHA,
  makeLeaderboard,
  makeMyScoring,
  makeOptions,
  makeRow,
  PRIYA,
  RAVI,
} from './testFixtures'

const ME = makeMe({ handle: PRIYA.handle, display_name: PRIYA.display_name })
const KORA = { code: 'W150', name: 'Koramangala', kind: 'ward' as const }
const NO_HEROES = { region: KORA, items: [] }

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /leaderboards': { body: makeLeaderboard() },
    'GET /leaderboards/heroes': { body: NO_HEROES },
    'GET /scoring/me': { body: makeMyScoring() },
    'GET /scoring/options': { body: makeOptions() },
    ...handlers,
  })
}

const finalWard = (overrides: Parameters<typeof makeLeaderboard>[0] = {}) =>
  makeLeaderboard({
    region: KORA,
    month: '2026-10',
    final: true,
    finalizes_at: null,
    titles: [],
    me: null,
    ...overrides,
  })

describe('No Kindness Hero copy (§3.4 item 11)', () => {
  it('names the ward minimums from the options', async () => {
    setup({ 'GET /leaderboards': { body: finalWard() } })
    renderApp('/leaderboard/W150?month=2026-10')
    expect(
      await screen.findByText(
        'No Kindness Hero this month: a Hero needs at least 3 deeds recognised by 2 different people.',
      ),
    ).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Kindness Heroes' })).not.toBeInTheDocument()
  })

  it('follows a changed config instead of fixed numbers', async () => {
    const options = makeOptions()
    setup({
      'GET /leaderboards': { body: finalWard() },
      'GET /scoring/options': {
        body: {
          ...options,
          levels: options.levels.map((l) =>
            l.kind === 'ward' ? { ...l, hero_min_deeds: 5, hero_min_reporters: 4 } : l,
          ),
        },
      },
    })
    renderApp('/leaderboard/W150?month=2026-10')
    expect(
      await screen.findByText(/a Hero needs at least 5 deeds recognised by 4 different people\.$/),
    ).toBeVisible()
  })

  it('falls back to the general wording when the options can’t load or lack the minimums', async () => {
    setup({
      'GET /leaderboards': { body: finalWard() },
      'GET /scoring/options': { status: 500, body: { detail: 'x', code: 'server_error' } },
    })
    renderApp('/leaderboard/W150?month=2026-10')
    expect(await screen.findByText(/^No Kindness Hero this month: nobody here/)).toBeVisible()
    expect(screen.queryByText(/3 deeds/)).not.toBeInTheDocument()

    expect(noHeroText(undefined)).toMatch(/^No Kindness Hero this month: nobody here/)
    expect(noHeroText({ kind: 'ward', enabled: true })).toMatch(/nobody here/)
    expect(
      noHeroText({ kind: 'ward', enabled: true, hero_min_deeds: 1, hero_min_reporters: 1 }),
    ).toBe('No Kindness Hero this month: a Hero needs at least 1 deed recognised by 1 person.')
  })

  it('says nothing on a final city board without titles (no minimum there)', async () => {
    setup({
      'GET /leaderboards': {
        body: finalWard({
          region: { code: 'IN-KA-BLR', name: 'Bangalore', kind: 'city' },
          items: [],
        }),
      },
    })
    renderApp('/leaderboard?month=2026-10')
    expect(await screen.findByText(/No one is on this board yet\./)).toBeVisible()
    expect(screen.queryByText(/No Kindness Hero/)).not.toBeInTheDocument()
  })

  it('never shows the copy on a provisional board', async () => {
    setup({
      'GET /leaderboards': {
        body: finalWard({ final: false, finalizes_at: '2026-12-01T19:30:00Z' }),
      },
    })
    renderApp('/leaderboard/W150')
    expect(await screen.findByText('Provisional until 2 Dec, 01:00 IST.')).toBeVisible()
    expect(screen.queryByText(/No Kindness Hero/)).not.toBeInTheDocument()
  })
})

describe('Shared places and titles (D67, Q58)', () => {
  it('a shared #1 shows "=1" for both, both carry the title, and the next place is 2', async () => {
    const hero = 'Kindness Hero · Koramangala · October 2026'
    setup({
      'GET /leaderboards': {
        body: finalWard({
          items: [
            makeRow({ rank: 1, shared: true, user: ASHA }),
            makeRow({ rank: 1, shared: true, user: RAVI }),
            makeRow({ rank: 2, user: AHMED, deed_count: 3 }),
          ],
          titles: [{ place: 1, label: hero, users: [ASHA, RAVI] }],
        }),
      },
    })
    renderApp('/leaderboard/W150?month=2026-10')
    const rows = within(await screen.findByRole('list', { name: 'Ranking' })).getAllByTestId(
      'board-row',
    )
    expect(rows.map((r) => within(r).getByLabelText(/^Rank/).textContent)).toEqual([
      '=1',
      '=1',
      '2',
    ])
    expect(within(rows[0]).getByLabelText('Rank 1, shared')).toBeVisible()
    expect(rows[0]).toHaveTextContent(hero)
    expect(rows[1]).toHaveTextContent(hero)
    expect(rows[2]).not.toHaveTextContent('Kindness Hero')
    const heroes = screen.getByRole('heading', { name: 'Kindness Heroes' }).closest('section')
    expect(heroes).toHaveTextContent(`#1 · ${hero}`)
    expect(heroes).toHaveTextContent('Asha Rao, Ravi Kumar')
  })

  it('shows no titles or title markers on a provisional board, even if the reply had some', async () => {
    setup({
      'GET /leaderboards': {
        body: makeLeaderboard({
          titles: [{ place: 1, label: 'Bangalore City Kindness Hero', users: [ASHA] }],
        }),
      },
    })
    renderApp('/leaderboard')
    const ranking = await screen.findByRole('list', { name: 'Ranking' })
    expect(screen.getByText(/^Provisional until/)).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Kindness Heroes' })).not.toBeInTheDocument()
    expect(ranking).not.toHaveTextContent('City Kindness Hero')
  })

  it('a final board says Final even if a stale finalizes_at came along', async () => {
    setup({
      'GET /leaderboards': {
        body: makeLeaderboard({ final: true, finalizes_at: '2026-12-01T19:30:00Z' }),
      },
    })
    renderApp('/leaderboard')
    expect(await screen.findByText('Final.')).toBeVisible()
    expect(screen.queryByText(/Provisional/)).not.toBeInTheDocument()
  })
})

describe('Privacy of the heroes history (D69)', () => {
  it('never renders points carried by mistake in the heroes reply', async () => {
    setup({
      'GET /leaderboards/heroes': {
        body: {
          region: KORA,
          items: [
            {
              period_month: '2026-10',
              points: 4321,
              titles: [
                {
                  place: 1,
                  label: 'Kindness Hero · Koramangala · October 2026',
                  users: [{ ...ASHA, points: 8765, doer_points: 5432 }],
                  rank_score: 6543,
                },
              ],
            },
          ],
        },
      },
    })
    renderApp('/leaderboard/W150')
    const history = (await screen.findByRole('heading', { name: 'Past Kindness Heroes' })).closest(
      'section',
    )
    expect(history).not.toBeNull()
    expect(await within(history as HTMLElement).findByText('Asha Rao')).toBeVisible()
    const main = screen.getByRole('main')
    expect(main).not.toHaveTextContent(/4321|8765|5432|6543/)
    expect(within(main).queryByText(/\bpoints?\b/i)).not.toBeInTheDocument()
  })
})
