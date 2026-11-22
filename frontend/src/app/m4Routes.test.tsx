/** M4 routes (M4-DESIGN §6.2): placeholders until F16 / F17 build the pages. */

import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../test/utils'

const ROUTES = [
  ['/me/points', 'My points'],
  ['/leaderboard', 'Leaderboard'],
  ['/leaderboard/IN-KA-BLR-W150', 'Leaderboard'],
] as const

describe('M4 routes (PLACEHOLDER M4 F14)', () => {
  it.each(ROUTES)('%s sends a signed-out visitor to login with next', async (path) => {
    mockApi()
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(path)}`)
  })

  it.each(ROUTES)('%s shows its placeholder when signed in', async (path, heading) => {
    mockApi(signedIn(makeMe()))
    renderApp(path)
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('This page is coming soon.')
  })
})
