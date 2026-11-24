import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, makeQr, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'

const needsPhone = makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })
const START = {
  phone_e164: '+919876543210',
  expires_at: '2026-11-11T12:10:00Z',
  resend_after_seconds: 30,
}

async function sendCode(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Indian mobile number'), '98765 43210')
  await user.click(screen.getByRole('button', { name: 'Send code' }))
}

describe('OnboardingPage phone step', () => {
  it('sends a code, verifies it and goes to the QR page', async () => {
    const api = mockApi({
      ...signedIn(needsPhone),
      'POST /users/me/phone/start': { body: START },
      'POST /users/me/phone/verify': { body: makeMe() },
      'GET /users/me/qr': { body: makeQr() },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/onboarding')

    await sendCode(user)
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Enter the 6-digit code we sent to +919876543210.',
    )
    expect(api.calls('POST /users/me/phone/start')[0].body).toEqual({ phone: '98765 43210' })
    expect(screen.getByRole('button', { name: /resend code in \d+s/i })).toBeDisabled()

    await user.type(screen.getByLabelText('6-digit code'), '123456')
    await user.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/me/qr'))
    expect(api.calls('POST /users/me/phone/verify')[0].body).toEqual({
      phone: '+919876543210',
      code: '123456',
    })
    expect(await screen.findByText('GD-7K3M9Q0')).toBeInTheDocument()
  })

  it('shows the error for a wrong code and lets the user try again', async () => {
    mockApi({
      ...signedIn(needsPhone),
      'POST /users/me/phone/start': { body: START },
      'POST /users/me/phone/verify': {
        status: 400,
        body: { detail: 'That code is not correct.', code: 'otp_incorrect' },
      },
    })
    const user = userEvent.setup()
    renderApp('/onboarding')
    await sendCode(user)
    await user.type(await screen.findByLabelText('6-digit code'), '000000')
    await user.click(screen.getByRole('button', { name: 'Verify' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('That code is not correct.')
    expect(screen.getByRole('button', { name: 'Verify' })).toBeEnabled()
  })

  it('asks for all 6 digits before calling the API', async () => {
    const api = mockApi({
      ...signedIn(needsPhone),
      'POST /users/me/phone/start': { body: START },
    })
    const user = userEvent.setup()
    renderApp('/onboarding')
    await sendCode(user)
    await user.type(await screen.findByLabelText('6-digit code'), '12a3')
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Please enter the 6-digit code')
    expect(api.calls('POST /users/me/phone/verify')).toHaveLength(0)
  })

  it('explains that only Indian mobile numbers work', async () => {
    mockApi({
      ...signedIn(needsPhone),
      'POST /users/me/phone/start': {
        status: 400,
        body: { detail: 'Please enter a valid Indian mobile number.', code: 'invalid_phone' },
      },
    })
    const user = userEvent.setup()
    renderApp('/onboarding')
    await sendCode(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Please enter a valid Indian mobile number.',
    )
    expect(screen.getByLabelText('Indian mobile number')).toBeInTheDocument()
  })

  it('shows rate-limit messages without retrying', async () => {
    const api = mockApi({
      ...signedIn(needsPhone),
      'POST /users/me/phone/start': {
        status: 429,
        body: { detail: 'Too many requests. Please wait a minute.', code: 'rate_limited' },
      },
    })
    const user = userEvent.setup()
    renderApp('/onboarding')
    await sendCode(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Too many requests. Please wait a minute.',
    )
    expect(api.calls('POST /users/me/phone/start')).toHaveLength(1)
  })

  it('goes back to number entry when changing the number', async () => {
    mockApi({ ...signedIn(needsPhone), 'POST /users/me/phone/start': { body: START } })
    const user = userEvent.setup()
    renderApp('/onboarding')
    await sendCode(user)
    await user.click(await screen.findByRole('button', { name: 'Change number' }))
    expect(screen.getByLabelText('Indian mobile number')).toHaveValue('98765 43210')
    expect(screen.queryByLabelText('6-digit code')).not.toBeInTheDocument()
  })

  it('enables resend once the countdown ends', async () => {
    const api = mockApi({
      ...signedIn(needsPhone),
      'POST /users/me/phone/start': { body: { ...START, resend_after_seconds: 1 } },
    })
    const user = userEvent.setup()
    renderApp('/onboarding')
    await sendCode(user)
    const resend = await screen.findByRole('button', { name: 'Resend code' }, { timeout: 3000 })
    await user.click(resend)
    await waitFor(() => expect(api.calls('POST /users/me/phone/start')).toHaveLength(2))
    expect(api.calls('POST /users/me/phone/start')[1].body).toEqual({ phone: '+919876543210' })
  })
})

describe('OnboardingPage age step', () => {
  it('asks social sign-ups for age and consent first', async () => {
    const api = mockApi({
      ...signedIn(
        makeMe({
          phone_e164: null,
          onboarding: { adult: false, consents: false, phone: false, complete: false },
        }),
      ),
      'POST /users/me/age-consent': { body: needsPhone },
    })
    const user = userEvent.setup()
    renderApp('/onboarding')

    await user.type(await screen.findByLabelText('Date of birth'), '1990-05-01')
    await user.click(screen.getByRole('checkbox', { name: /i agree to the terms/i }))
    await user.click(screen.getByRole('checkbox', { name: /privacy notice/i }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(await screen.findByLabelText('Indian mobile number')).toBeInTheDocument()
    expect(api.calls('POST /users/me/age-consent')[0].body).toEqual({
      date_of_birth: '1990-05-01',
      accept_terms: true,
      accept_privacy: true,
    })
  })
})
