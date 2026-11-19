/** M3 routes (M3-DESIGN §6.2): guards and the F9 placeholders the later tasks replace. */

import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../test/utils'

const notOnboarded = () => makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })

const AUTH_ROUTES = [
  ['/deeds/d1', 'F11'],
  ['/me/saved', 'F13'],
  ['/me/connections?tab=following', 'F13'],
] as const

describe('M3 RequireAuth routes', () => {
  it.each(AUTH_ROUTES)('%s sends a signed-out visitor to login with next', async (path) => {
    mockApi()
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(path)}`)
  })

  it.each(AUTH_ROUTES)('%s is open before onboarding (D44)', async (path, task) => {
    mockApi(signedIn(notOnboarded()))
    renderApp(path)
    expect(await screen.findByRole('note')).toHaveAttribute('data-placeholder', task)
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
    mockApi()
    const { router } = renderApp('/d/d1')
    expect(await screen.findByRole('note')).toHaveAttribute('data-placeholder', 'F12')
    expect(router.state.location.pathname).toBe('/d/d1')
  })

  it('sends a signed-in visitor from /d/:id to /deeds/:id', async () => {
    mockApi(signedIn(makeMe()))
    const { router } = renderApp('/d/d1')
    await waitFor(() => expect(router.state.location.pathname).toBe('/deeds/d1'))
  })
})
