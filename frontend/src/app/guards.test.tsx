import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../test/utils'

describe('route guards', () => {
  it('sends logged-out visitors to login and remembers where they were going', async () => {
    mockApi()
    const { router } = renderApp('/me/qr')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe('?next=%2Fme%2Fqr')
    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
  })

  it('protects the profile page', async () => {
    mockApi()
    const { router } = renderApp('/profile')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  })

  it('sends signed-in users without a verified phone to onboarding', async () => {
    mockApi(signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })))
    const { router } = renderApp('/me/qr')
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
    expect(router.state.location.search).toBe('?next=%2Fme%2Fqr')
  })

  it('lets onboarding-incomplete users see their profile', async () => {
    mockApi(signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })))
    renderApp('/profile')
    expect(await screen.findByRole('heading', { name: 'Your profile' })).toBeInTheDocument()
  })

  it('redirects signed-in users away from login', async () => {
    mockApi(signedIn(makeMe()))
    const { router } = renderApp('/login')
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
  })

  it('sends a finished user from onboarding to their QR code', async () => {
    mockApi({ ...signedIn(makeMe()), 'GET /users/me/qr': { status: 500, body: {} } })
    const { router } = renderApp('/onboarding')
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/qr'))
  })

  it('logs out from the header', async () => {
    const api = mockApi({ ...signedIn(makeMe()), 'POST /auth/logout': { status: 204 } })
    const user = userEvent.setup()
    renderApp('/profile')
    await user.click(await screen.findByRole('button', { name: 'Log out' }))
    expect(await screen.findByRole('link', { name: 'Sign up' }, { timeout: 2000 })).toBeVisible()
    expect(api.calls('POST /auth/logout')).toHaveLength(1)
  })
})
