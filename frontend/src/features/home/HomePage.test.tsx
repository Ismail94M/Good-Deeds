import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'

describe('HomePage', () => {
  it('shows the tagline', () => {
    mockApi()
    renderApp('/')
    expect(screen.getByRole('heading', { name: /make kindness visible/i })).toBeInTheDocument()
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
    expect(within(main).getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login')
  })

  it('welcomes signed-in users with quick links', async () => {
    mockApi(signedIn(makeMe({ onboarding: NOT_ONBOARDED })))
    renderApp('/')
    expect(await screen.findByText('Welcome, Asha!')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'My QR' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'Finish setting up' })).toHaveAttribute(
      'href',
      '/onboarding',
    )
  })
})
