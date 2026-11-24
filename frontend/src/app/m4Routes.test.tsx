/** M4 routes (M4-DESIGN §6.2): My points and the leaderboards need a signed-in user. */

import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../test/utils'

const ROUTES = [
  ['/me/points', 'My points'],
  ['/leaderboard', 'Area leaderboard'],
  ['/leaderboard/IN-KA-BLR-W150', 'Koramangala'],
] as const

const board = (name: string, kind: string) => ({
  region: { code: name, name, kind },
  month: '2026-11',
  computed_at: '2026-11-22T10:00:00Z',
  final: false,
  items: [],
  me: null,
  titles: [],
  next_cursor: null,
})

describe('M4 routes', () => {
  it.each(ROUTES)('%s sends a signed-out visitor to login with next', async (path) => {
    mockApi()
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(path)}`)
  })

  it.each(ROUTES)('%s shows its page when signed in', async (path, heading) => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /leaderboards': (req) => ({
        body: req.query.get('region') ? board('Koramangala', 'ward') : board('Bangalore', 'city'),
      }),
    })
    renderApp(path)
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })
})
