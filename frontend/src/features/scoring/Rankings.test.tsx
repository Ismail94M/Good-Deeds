import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { UserRanks } from './scoringApi'

/**
 * D76 (PO 2026-11-24): City Rank beside Verified Deeds on profiles, and "View all rankings" with
 * Area, City, State and National. Ranks come from `GET /scoring/users/{handle}/ranks`, which reads
 * the boards' own snapshots; nothing here invents a number.
 */

const ASHA = {
  handle: 'asha',
  display_name: 'Asha Rao',
  city: 'Bangalore',
  area: 'Jayanagar',
  bio: null,
  joined_at: '2026-11-01T10:00:00Z',
}

function ranks(overrides: Partial<Record<string, Partial<UserRanks['levels'][number]>>> = {}) {
  const base: UserRanks = {
    month: '2026-11',
    final: false,
    levels: [
      {
        level: 'ward',
        enabled: true,
        region: { code: 'IN-KA-BLR-W150', name: 'Koramangala', kind: 'ward' },
        rank: 1,
        shared: false,
      },
      {
        level: 'city',
        enabled: true,
        region: { code: 'IN-KA-BLR', name: 'Bangalore', kind: 'city' },
        rank: 24,
        shared: false,
      },
      { level: 'state', enabled: false, region: null, rank: null, shared: false },
      { level: 'country', enabled: false, region: null, rank: null, shared: false },
    ],
  }
  return {
    ...base,
    levels: base.levels.map((lvl) => ({ ...lvl, ...overrides[lvl.level] })),
  }
}

function publicProfile(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'GET /users/asha': { body: ASHA },
    'GET /users/asha/deed-counts': { body: { verified: 31 } },
    'GET /users/asha/social': {
      body: { followers: 2, following: 3, is_following: false, is_me: false },
    },
    'GET /feed/users/asha/deeds': { body: { items: [], next_cursor: null } },
    ...extra,
  })
}

describe('Profile rankings (D76)', () => {
  it('signed in: City Rank sits beside Verified Deeds, and the panel lists all four levels', async () => {
    publicProfile({
      ...signedIn(makeMe({ handle: 'priya', display_name: 'Priya Sharma' })),
      'GET /scoring/users/asha/ranks': { body: ranks() },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    await within(counts).findByText('#24')
    const labels = within(counts)
      .getAllByText(/^(Verified Deeds|City Rank|Badges|Hero titles|Followers|Following)$/)
      .map((el) => el.textContent)
    expect(labels.slice(0, 2)).toEqual(['Verified Deeds', 'City Rank'])

    await userEvent.click(screen.getByRole('button', { name: 'View all rankings' }))
    const dialog = screen.getByRole('dialog', { name: 'Asha Rao’s rankings' })
    expect(within(dialog).getByText('November 2026 · Provisional')).toBeVisible()
    const rows = within(within(dialog).getByRole('list', { name: 'Rankings' })).getAllByRole(
      'listitem',
    )
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual([
      'rank-ward',
      'rank-city',
      'rank-state',
      'rank-country',
    ])
    expect(
      within(rows[0]).getByRole('link', { name: /Area rank #1 in Koramangala/ }),
    ).toHaveAttribute('href', '/leaderboard/IN-KA-BLR-W150?month=2026-11')
    expect(within(rows[1]).getByRole('link', { name: /City rank #24 in Bangalore/ })).toBeVisible()
    for (const row of rows.slice(2)) {
      expect(row).toHaveTextContent('Coming soon')
      expect(within(row).queryByRole('link')).not.toBeInTheDocument()
      expect(row).not.toHaveTextContent(/#\d/)
    }
    expect(rows[2]).toHaveTextContent('State rank')
    expect(rows[3]).toHaveTextContent('National rank')
    expect(dialog).toHaveTextContent(
      'then points, then the number of different witnesses, then the number of different days',
    )
    // The registered area (Jayanagar) is never shown; only the board's own region is.
    expect(document.body).not.toHaveTextContent('Jayanagar')

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View all rankings' })).toHaveFocus()
  })

  it('shows "Not ranked yet" and no number when they are on no board this month', async () => {
    publicProfile({
      ...signedIn(makeMe({ handle: 'priya', display_name: 'Priya Sharma' })),
      'GET /scoring/users/asha/ranks': {
        body: ranks({ ward: { rank: null, region: null }, city: { rank: null, region: null } }),
      },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    await waitFor(() =>
      expect(within(counts).getByText(/Not ranked yet this month/)).toBeInTheDocument(),
    )
    await userEvent.click(screen.getByRole('button', { name: 'View all rankings' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByTestId('rank-ward')).toHaveTextContent('Not ranked yet this month')
    expect(within(dialog).getByTestId('rank-city')).toHaveTextContent('Not ranked yet this month')
    expect(dialog).not.toHaveTextContent(/#\d/)
    expect(within(dialog).queryByRole('link')).not.toBeInTheDocument()
  })

  it('marks a shared place "=3", as the boards do', async () => {
    publicProfile({
      ...signedIn(makeMe({ handle: 'priya', display_name: 'Priya Sharma' })),
      'GET /scoring/users/asha/ranks': { body: ranks({ city: { rank: 3, shared: true } }) },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    expect(await within(counts).findByText('=3')).toBeVisible()
  })

  it('signed out: no ranks are asked for or shown', async () => {
    const api = publicProfile({
      'GET /scoring/users/asha/ranks': { body: ranks() },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    expect(await within(counts).findByText('31')).toBeVisible()
    expect(within(counts).queryByText('City Rank')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'View all rankings' })).not.toBeInTheDocument()
    expect(api.calls('GET /scoring/users/asha/ranks')).toHaveLength(0)
  })

  it('own profile: Self-log Deeds, Verified Deeds and City Rank in one row, then the button', async () => {
    mockApi({
      ...signedIn(makeMe({ display_name: 'Asha Rao' })),
      'GET /users/asha/deed-counts': { body: { verified: 12 } },
      'GET /me/self-logs': { body: { items: [], next_cursor: null, total: 25 } },
      'GET /scoring/users/asha/ranks': { body: ranks() },
    })
    renderApp('/profile')
    const counts = await screen.findByRole('group', { name: 'Deed counts' })
    const links = await within(counts).findAllByRole('link')
    await waitFor(() =>
      expect(links.map((l) => l.textContent)).toEqual([
        '25 Self-log Deeds',
        '12 Verified Deeds',
        '#24 City Rank',
      ]),
    )
    expect(within(counts).getByRole('link', { name: /#24 City Rank/ })).toHaveAttribute(
      'href',
      '/leaderboard/IN-KA-BLR',
    )
    await userEvent.click(screen.getByRole('button', { name: 'View all rankings' }))
    expect(screen.getByRole('dialog', { name: 'Your rankings' })).toBeVisible()
  })
})
