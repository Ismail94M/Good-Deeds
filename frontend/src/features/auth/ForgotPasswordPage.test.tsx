import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { maskPhone } from './validation'

type User = ReturnType<typeof userEvent.setup>

const PHONE = '98765 44630'
const CODE = '482913'
const PASSWORD = 'kindness-forever'

async function sendCode(user: User, phone = PHONE) {
  await user.type(await screen.findByLabelText('Mobile number'), phone)
  await user.click(screen.getByRole('button', { name: 'Send code' }))
}

async function fillReset(user: User, confirm = PASSWORD) {
  await user.type(await screen.findByLabelText('6-digit code'), CODE)
  await user.type(screen.getByLabelText('New password'), PASSWORD)
  await user.type(screen.getByLabelText('Confirm new password'), confirm)
  await user.click(screen.getByRole('button', { name: 'Reset password' }))
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Sign-in page', () => {
  it('links to forgot password', async () => {
    mockApi()
    const user = userEvent.setup()
    const { router } = renderApp('/login')
    const link = await screen.findByRole('link', { name: 'Forgot password?' })
    expect(link).toHaveAttribute('href', '/forgot-password')
    expect(link.className).toContain('min-h-11')
    await user.click(link)
    expect(await screen.findByRole('heading', { name: 'Reset your password' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/forgot-password')
  })
})

describe('ForgotPasswordPage', () => {
  it('steps from the phone number to the code and new password', async () => {
    const api = mockApi({ 'POST /auth/password/forgot': { body: { sent: true } } })
    const user = userEvent.setup()
    renderApp('/forgot-password')

    expect(
      await screen.findByText(
        "Enter the mobile number on your Good Deeds account. We'll text you a 6-digit code.",
      ),
    ).toBeVisible()
    expect(
      screen.getByText('Signed up with Google? Use Continue with Google instead.'),
    ).toBeVisible()
    await sendCode(user)

    const code = await screen.findByLabelText('6-digit code')
    expect(code).toHaveAttribute('inputmode', 'numeric')
    expect(code).toHaveAttribute('autocomplete', 'one-time-code')
    expect(screen.getByText(/If an account uses/)).toHaveTextContent('+91 ••••• •4630')
    expect(screen.getByLabelText('New password')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getAllByText('At least 10 characters.').length).toBeGreaterThan(0)
    // Same cooldown as phone verification.
    expect(screen.getByRole('button', { name: /Resend code in \d+s/ })).toBeDisabled()
    expect(api.calls('POST /auth/password/forgot')[0].body).toEqual({ phone: PHONE })
  })

  it('checks the phone format before calling the API', async () => {
    const api = mockApi()
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user, '123')
    expect(screen.getByRole('alert')).toHaveTextContent(/Indian mobile number/)
    expect(api.calls('POST /auth/password/forgot')).toHaveLength(0)
  })

  it('shows the same message whether or not the number has an account', async () => {
    // The API always answers 200 {sent: true}; the page must not hint either way.
    mockApi({ 'POST /auth/password/forgot': { body: { sent: true } } })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user, '+91 91234 50001')
    const first = (await screen.findByText(/If an account uses/)).textContent
    await user.click(screen.getByRole('button', { name: 'Use a different number' }))
    await sendCode(user, '+91 91234 59999')
    const second = (await screen.findByText(/If an account uses/)).textContent
    expect(first?.replace(/\d{4}/g, 'NNNN')).toBe(second?.replace(/\d{4}/g, 'NNNN'))
    expect(second).toContain("we've sent a code.")
  })

  it('goes back to the phone step with "Use a different number"', async () => {
    mockApi({ 'POST /auth/password/forgot': { body: { sent: true } } })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    await user.click(await screen.findByRole('button', { name: 'Use a different number' }))
    expect(await screen.findByLabelText('Mobile number')).toBeVisible()
  })

  it('blocks mismatched passwords without calling the API', async () => {
    const api = mockApi({ 'POST /auth/password/forgot': { body: { sent: true } } })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    await fillReset(user, 'something-else-1')
    expect(await screen.findByText(/passwords don't match/)).toBeVisible()
    expect(screen.getByLabelText('Confirm new password')).toHaveAttribute('aria-invalid', 'true')
    expect(api.calls('POST /auth/password/reset')).toHaveLength(0)
  })

  it('applies the sign-up password rules', async () => {
    const api = mockApi({ 'POST /auth/password/forgot': { body: { sent: true } } })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    await user.type(await screen.findByLabelText('6-digit code'), CODE)
    await user.type(screen.getByLabelText('New password'), 'short')
    await user.type(screen.getByLabelText('Confirm new password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Reset password' }))
    expect(await screen.findByText('Your password needs at least 10 characters.')).toBeVisible()
    expect(api.calls('POST /auth/password/reset')).toHaveLength(0)
  })

  it('shows the API password-policy error on the password field', async () => {
    mockApi({
      'POST /auth/password/forgot': { body: { sent: true } },
      'POST /auth/password/reset': {
        status: 422,
        body: {
          detail: [
            {
              loc: ['body', 'new_password'],
              msg: 'String should have at least 10 characters',
            },
          ],
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    await fillReset(user)
    expect(await screen.findByText('String should have at least 10 characters')).toBeVisible()
    expect(screen.getByLabelText('New password')).toHaveAttribute('aria-invalid', 'true')
  })

  it('explains a wrong or expired code', async () => {
    mockApi({
      'POST /auth/password/forgot': { body: { sent: true } },
      'POST /auth/password/reset': {
        status: 400,
        body: {
          detail: 'That code is not correct or has expired. Please request a new one.',
          code: 'otp_incorrect',
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    await fillReset(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That code is wrong or has expired. Check it or send a new one.',
    )
  })

  it('shows the rate-limit message when sending is limited', async () => {
    mockApi({
      'POST /auth/password/forgot': {
        status: 429,
        body: {
          detail: 'Too many attempts. Please wait a while and try again.',
          code: 'rate_limited',
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Too many attempts. Please wait a while and try again.',
    )
    expect(screen.getByLabelText('Mobile number')).toBeVisible()
  })

  it('shows the rate-limit message when resetting is limited', async () => {
    mockApi({
      'POST /auth/password/forgot': { body: { sent: true } },
      'POST /auth/password/reset': {
        status: 429,
        body: {
          detail: 'Too many attempts. Please wait a while and try again.',
          code: 'rate_limited',
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    await fillReset(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many attempts.')
  })

  it('shows the connection message when offline', async () => {
    mockApi({
      'POST /auth/password/forgot': () => {
        throw new TypeError('Failed to fetch')
      },
    })
    const user = userEvent.setup()
    renderApp('/forgot-password')
    await sendCode(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(/check your connection/i)
  })

  it('sends the user to sign in with a notice after a reset', async () => {
    const api = mockApi({
      'POST /auth/password/forgot': { body: { sent: true } },
      'POST /auth/password/reset': { body: { reset: true } },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/forgot-password')
    await sendCode(user)
    await fillReset(user)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(await screen.findByRole('status')).toHaveTextContent(
      "Password changed. Sign in with your new password. You've been signed out on your other devices.",
    )
    expect(api.calls('POST /auth/password/reset')[0].body).toEqual({
      phone: PHONE,
      code: CODE,
      new_password: PASSWORD,
    })
    // The reset doesn't sign the user in.
    expect(api.calls('POST /auth/login')).toHaveLength(0)
  })

  it('redirects signed-in users away', async () => {
    mockApi(signedIn(makeMe()))
    const { router } = renderApp('/forgot-password')
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
  })

  it('keeps the phone, code and password out of the URL and storage', async () => {
    mockApi({
      'POST /auth/password/forgot': { body: { sent: true } },
      'POST /auth/password/reset': { body: { reset: true } },
    })
    const writes: string[] = []
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      writes.push(`${key}=${value}`)
    })
    const user = userEvent.setup()
    const { router } = renderApp('/forgot-password')
    const seen: string[] = []
    router.subscribe((state) => {
      const { pathname, search, hash } = state.location
      seen.push(pathname + search + hash + JSON.stringify(state.location.state ?? null))
    })
    await sendCode(user)
    await fillReset(user)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))

    const secrets = ['98765', '44630', CODE, PASSWORD]
    for (const entry of [...seen, ...writes, window.location.href]) {
      for (const secret of secrets) expect(entry).not.toContain(secret)
    }
    expect(router.state.location.state).toEqual({ passwordReset: true })
  })
})

describe('maskPhone', () => {
  it('keeps only the last four digits', () => {
    expect(maskPhone('+91 98765 44630')).toBe('+91 ••••• •4630')
    expect(maskPhone('9876544630')).toBe('+91 ••••• •4630')
  })
})
