import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, TOKEN } from '../../../test/utils'
import { facebookLogin, prepareFacebook } from './facebook'
import { renderGoogleButton } from './google'

vi.mock('./google', () => ({ renderGoogleButton: vi.fn() }))
vi.mock('./facebook', () => ({ prepareFacebook: vi.fn(), facebookLogin: vi.fn() }))

describe('SocialButtons', () => {
  beforeEach(() => {
    vi.mocked(renderGoogleButton).mockResolvedValue(undefined)
    vi.mocked(prepareFacebook).mockResolvedValue(undefined)
    vi.mocked(facebookLogin).mockResolvedValue('fb-token-123456')
  })

  it('hides social buttons when no provider is configured', async () => {
    mockApi()
    renderApp('/login')
    await screen.findByRole('heading', { name: 'Welcome back' })
    expect(screen.queryByRole('button', { name: /facebook/i })).not.toBeInTheDocument()
    expect(screen.queryByTestId('google-button')).not.toBeInTheDocument()
    expect(renderGoogleButton).not.toHaveBeenCalled()
  })

  it('signs in with Facebook when configured', async () => {
    const api = mockApi({
      'GET /auth/config': { body: { google_client_id: 'g-id', facebook_app_id: 'fb-id' } },
      'POST /auth/oauth/facebook': { body: TOKEN },
      'GET /users/me': { body: makeMe() },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/login')

    const button = await screen.findByRole('button', { name: 'Continue with Facebook' })
    expect(screen.getByTestId('google-button')).toBeInTheDocument()
    await waitFor(() => expect(prepareFacebook).toHaveBeenCalledWith('fb-id'))
    expect(renderGoogleButton).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      'g-id',
      expect.any(Function),
      expect.any(Function),
    )

    await user.click(button)
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(api.calls('POST /auth/oauth/facebook')[0].body).toEqual({ token: 'fb-token-123456' })
  })

  it('shows the API message when the email already has an account', async () => {
    mockApi({
      'GET /auth/config': { body: { google_client_id: null, facebook_app_id: 'fb-id' } },
      'POST /auth/oauth/facebook': {
        status: 409,
        body: { detail: 'Log in the way you did before.', code: 'email_exists' },
      },
    })
    const user = userEvent.setup()
    renderApp('/signup')
    const button = await screen.findByRole('button', { name: 'Continue with Facebook' })
    await waitFor(() => expect(prepareFacebook).toHaveBeenCalled())
    await user.click(button)
    expect(await screen.findByRole('alert')).toHaveTextContent('Log in the way you did before.')
  })

  it('fails gracefully when the provider SDK cannot load', async () => {
    vi.mocked(prepareFacebook).mockRejectedValue(new Error('blocked'))
    vi.mocked(renderGoogleButton).mockRejectedValue(new Error('blocked'))
    mockApi({
      'GET /auth/config': { body: { google_client_id: 'g-id', facebook_app_id: 'fb-id' } },
    })
    renderApp('/login')
    expect(
      await screen.findByRole('button', { name: 'Facebook sign-in unavailable' }),
    ).toBeDisabled()
    expect(await screen.findByRole('alert')).toHaveTextContent(/use email instead/i)
  })
})
