import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, TOKEN } from '../../test/utils'

async function logIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Email'), 'asha@example.com')
  await user.type(screen.getByLabelText('Password'), 'kindness-123')
  await user.click(screen.getByRole('button', { name: 'Log in' }))
}

describe('LoginPage', () => {
  it('shows the API error when the password is wrong', async () => {
    mockApi({
      'POST /auth/login': {
        status: 401,
        body: { detail: 'Incorrect email or password.', code: 'bad_credentials' },
      },
    })
    const user = userEvent.setup()
    renderApp('/login')
    await logIn(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.')
  })

  it('asks for both fields before calling the API', async () => {
    const api = mockApi()
    const user = userEvent.setup()
    renderApp('/login')
    await user.click(await screen.findByRole('button', { name: 'Log in' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Please enter your email and password.')
    expect(api.calls('POST /auth/login')).toHaveLength(0)
  })

  it('shows a friendly message when offline', async () => {
    mockApi({
      'POST /auth/login': () => {
        throw new TypeError('Failed to fetch')
      },
    })
    const user = userEvent.setup()
    renderApp('/login')
    await logIn(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(/check your connection/i)
  })

  it('returns to the page in ?next after logging in', async () => {
    mockApi({ 'POST /auth/login': { body: TOKEN }, 'GET /users/me': { body: makeMe() } })
    const user = userEvent.setup()
    const { router } = renderApp('/login?next=%2Fprofile')
    await logIn(user)
    await waitFor(() => expect(router.state.location.pathname).toBe('/profile'))
  })

  it('sends users who still need setup to onboarding', async () => {
    mockApi({
      'POST /auth/login': { body: TOKEN },
      'GET /users/me': { body: makeMe({ onboarding: NOT_ONBOARDED }) },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/login')
    await logIn(user)
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
  })

  it('ignores an off-site ?next', async () => {
    mockApi({ 'POST /auth/login': { body: TOKEN }, 'GET /users/me': { body: makeMe() } })
    const user = userEvent.setup()
    const { router } = renderApp('/login?next=%2F%2Fevil.example')
    await logIn(user)
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
  })
})
