import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, makeQr, mockApi, renderApp, signedIn } from '../../test/utils'

describe('MyQrPage', () => {
  it('shows the QR code and the ID for manual entry', async () => {
    mockApi({ ...signedIn(makeMe()), 'GET /users/me/qr': { body: makeQr() } })
    renderApp('/me/qr')

    expect(await screen.findByText('GD-7K3M9Q0')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Or share your ID' })).toBeInTheDocument()
    expect(screen.getByText(/they scan it to recognise you/i)).toBeInTheDocument()
    const qr = screen.getByRole('img', { name: 'QR code for GD-7K3M9Q0' })
    await waitFor(() => expect(qr.querySelector('svg')).not.toBeNull())
    expect(screen.getByText(/refreshes in \d+s/i)).toBeInTheDocument()
  })

  it('explains when onboarding is not finished', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /users/me/qr': {
        status: 403,
        body: {
          detail: 'Finish setting up your account to get your QR code.',
          code: 'onboarding_required',
        },
      },
    })
    renderApp('/me/qr')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Finish setting up your account to get your QR code.',
    )
    expect(screen.getByRole('link', { name: 'Finish setting up' })).toHaveAttribute(
      'href',
      '/onboarding',
    )
  })

  it('resets the QR only after confirming', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      'GET /users/me/qr': { body: makeQr() },
      'POST /users/me/qr/rotate': {
        body: makeQr({ scan_url: 'http://localhost:5173/scan/new', token: 'new' }),
      },
    })
    const user = userEvent.setup()
    renderApp('/me/qr')

    await user.click(await screen.findByRole('button', { name: 'Reset my QR' }))
    expect(api.calls('POST /users/me/qr/rotate')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Reset my QR' }))
    await user.click(screen.getByRole('button', { name: 'Yes, reset my QR' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Your QR code has been reset.')
    expect(api.calls('POST /users/me/qr/rotate')).toHaveLength(1)
  })

  it('fetches a new code when the server says to', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1 }) },
        { body: makeQr({ refresh_after_seconds: 30, token: 'second' }) },
      ],
    })
    renderApp('/me/qr')
    await screen.findByText('GD-7K3M9Q0')
    await waitFor(() => expect(api.calls('GET /users/me/qr')).toHaveLength(2), {
      timeout: 3000,
    })
  })
})
