/** M3 routes (M3-DESIGN §6.2): guards for the feed, deed, saved and connections pages. */

import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../test/utils'

const notOnboarded = () => makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })

const AUTH_ROUTES = [
  ['/deeds/d1', 'This deed isn’t available'],
  ['/me/saved', 'Saved'],
  ['/me/connections?tab=following', 'Asha'],
] as const

describe('M3 RequireAuth routes', () => {
  it.each(AUTH_ROUTES)('%s sends a signed-out visitor to login with next', async (path) => {
    mockApi()
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(path)}`)
  })

  it.each(AUTH_ROUTES)('%s is open before onboarding (D44)', async (path, heading) => {
    mockApi({
      ...signedIn(notOnboarded()),
      'GET /me/saved': { body: { items: [], next_cursor: null } },
      'GET /me/following': { body: { items: [], next_cursor: null } },
    })
    renderApp(path)
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
  })
})

describe('M3 public routes', () => {
  it.each([
    ['/community-guidelines', 'Community guidelines'],
    ['/photo-guidelines', 'Photo guidelines'],
  ])('%s is open to everyone', async (path, heading) => {
    mockApi()
    renderApp(path)
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
  })

  it('shows the public deed page to a signed-out visitor', async () => {
    mockApi({ 'GET /public/deeds/d1': { status: 404, body: { code: 'deed_unavailable' } } })
    const { router } = renderApp('/d/d1')
    expect(await screen.findByRole('heading', { name: 'This deed isn’t available' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/d/d1')
  })

  it('sends a signed-in visitor from /d/:id to /deeds/:id', async () => {
    mockApi(signedIn(makeMe()))
    const { router } = renderApp('/d/d1')
    await waitFor(() => expect(router.state.location.pathname).toBe('/deeds/d1'))
  })
})
