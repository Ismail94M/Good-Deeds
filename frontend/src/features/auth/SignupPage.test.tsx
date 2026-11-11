import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, TOKEN } from '../../test/utils'

function yearsAgo(years: number, extraDays = 0): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - years)
  d.setDate(d.getDate() + extraDays)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Your name'), 'Asha Rao')
  await user.type(screen.getByLabelText('Handle'), 'asha')
  await user.type(screen.getByLabelText('Email address'), 'asha@example.com')
  await user.type(screen.getByLabelText('Password'), 'kindness-123')
  await user.type(screen.getByLabelText('Date of birth'), yearsAgo(25))
  await user.click(screen.getByRole('checkbox', { name: /i agree to the terms/i }))
  await user.click(screen.getByRole('checkbox', { name: /privacy notice/i }))
}

describe('SignupPage', () => {
  it('shows validation messages for an empty form without calling the API', async () => {
    const api = mockApi()
    const user = userEvent.setup()
    renderApp('/signup')

    await user.click(await screen.findByRole('button', { name: 'Sign up' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Please fix the highlighted details.')
    expect(screen.getByText('Please enter your name.')).toBeInTheDocument()
    expect(screen.getByText('Please choose a handle.')).toBeInTheDocument()
    expect(screen.getByText('Please enter your email.')).toBeInTheDocument()
    expect(screen.getByText('Please choose a password.')).toBeInTheDocument()
    expect(screen.getByText('Please enter your date of birth.')).toBeInTheDocument()
    expect(screen.getByText('Please agree to the Terms to continue.')).toBeInTheDocument()
    expect(screen.getByLabelText('Handle')).toHaveAttribute('aria-invalid', 'true')
    expect(api.calls('POST /auth/signup')).toHaveLength(0)
  })

  it('checks handle and password rules', async () => {
    mockApi()
    const user = userEvent.setup()
    renderApp('/signup')
    await user.type(await screen.findByLabelText('Handle'), 'ab')
    await user.type(screen.getByLabelText('Password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Sign up' }))

    expect(screen.getByText('Your handle needs 3 to 30 characters.')).toBeInTheDocument()
    expect(screen.getByText('Your password needs at least 10 characters.')).toBeInTheDocument()
  })

  it('tells under-18s they cannot join yet', async () => {
    const api = mockApi()
    const user = userEvent.setup()
    renderApp('/signup')
    await fillValidForm(user)
    await user.clear(screen.getByLabelText('Date of birth'))
    await user.type(screen.getByLabelText('Date of birth'), yearsAgo(18, 1))

    expect(screen.getByText(/you must be 18 or older/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Sign up' }))
    expect(api.calls('POST /auth/signup')).toHaveLength(0)
  })

  it('shows the API error detail next to the field', async () => {
    mockApi({
      'POST /auth/signup': {
        status: 409,
        body: { detail: 'That handle is taken. Please choose another.', code: 'handle_taken' },
      },
    })
    const user = userEvent.setup()
    renderApp('/signup')
    await fillValidForm(user)
    await user.click(screen.getByRole('button', { name: 'Sign up' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('That handle is taken.')
    expect(screen.getByLabelText('Handle')).toHaveAttribute('aria-invalid', 'true')
  })

  it('signs up with the exact API fields and moves on to onboarding', async () => {
    const api = mockApi({
      'POST /auth/signup': { status: 201, body: TOKEN },
      'GET /users/me': { body: makeMe({ onboarding: NOT_ONBOARDED, phone_e164: null }) },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/signup')
    await fillValidForm(user)
    await user.click(screen.getByRole('button', { name: 'Sign up' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
    expect(api.calls('POST /auth/signup')[0].body).toEqual({
      display_name: 'Asha Rao',
      handle: 'asha',
      email: 'asha@example.com',
      password: 'kindness-123',
      date_of_birth: yearsAgo(25),
      accept_terms: true,
      accept_privacy: true,
    })
    expect(api.calls('GET /users/me')[0].headers.get('Authorization')).toBe('Bearer access-1')
    expect(await screen.findByRole('heading', { name: 'Verify your phone' })).toBeInTheDocument()
  })

  it('links the Terms and Privacy Notice from the consent boxes', async () => {
    mockApi()
    renderApp('/signup')
    const agreements = await screen.findByRole('group', { name: 'Agreements' })
    expect(within(agreements).getByRole('link', { name: 'Terms' })).toHaveAttribute(
      'href',
      '/terms',
    )
    expect(within(agreements).getByRole('link', { name: 'Privacy Notice' })).toHaveAttribute(
      'href',
      '/privacy',
    )
  })
})
