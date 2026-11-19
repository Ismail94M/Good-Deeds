import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'

describe('HomePage', () => {
  it('shows the tagline to signed-out visitors', async () => {
    mockApi()
    renderApp('/')
    expect(
      await screen.findByRole('heading', { name: /make kindness visible/i }),
    ).toBeInTheDocument()
  })

  it('reports a healthy API', async () => {
    const api = mockApi()
    renderApp('/')
    expect(await screen.findByText('All systems running')).toBeInTheDocument()
    expect(api.spy).toHaveBeenCalledWith('/api/health', expect.anything())
  })

  it('reports when the API is down', async () => {
    mockApi({
      'GET /health': { status: 503, body: { status: 'degraded', database: 'unavailable' } },
    })
    renderApp('/')
    expect(await screen.findByText('Service unavailable')).toBeInTheDocument()
  })

  it('invites logged-out visitors to sign up or log in', async () => {
    mockApi()
    renderApp('/')
    const main = await screen.findByRole('main')
    await waitFor(() => expect(within(main).getByRole('link', { name: 'Sign up' })).toBeVisible())
    expect(within(main).getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login')
  })

  it('shows the feed to signed-in users instead of the welcome page', async () => {
    mockApi(signedIn(makeMe({ onboarding: NOT_ONBOARDED })))
    renderApp('/')
    // PLACEHOLDER (M3 F9): F10 replaces this with the real feed's assertions.
    expect(await screen.findByRole('heading', { name: 'Good deeds' })).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveAttribute('data-placeholder', 'F10')
    expect(screen.queryByRole('heading', { name: /make kindness visible/i })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Sign up' })).toBeNull()
  })
})
