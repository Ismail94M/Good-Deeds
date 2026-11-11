import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'

describe('ProfilePage', () => {
  it('shows the profile with the phone number to its owner', async () => {
    mockApi(signedIn(makeMe({ area: 'Koramangala', bio: 'I like helping.' })))
    renderApp('/profile')
    expect(await screen.findByText('@asha')).toBeInTheDocument()
    expect(screen.getByText('Koramangala, Bangalore')).toBeInTheDocument()
    expect(screen.getByText('+919876543210')).toBeInTheDocument()
    expect(screen.getByText('GD-7K3M9Q0')).toBeInTheDocument()
    expect(screen.getByText(/to change your number, please contact support/i)).toBeInTheDocument()
  })

  it('does not show an ID before onboarding is complete', async () => {
    mockApi(signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })))
    renderApp('/profile')
    expect(await screen.findByText('@asha')).toBeInTheDocument()
    expect(screen.queryByText('Your ID')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Verify now' })).toHaveAttribute('href', '/onboarding')
  })

  it('edits the profile and counts bio characters', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      'PATCH /users/me': { body: makeMe({ area: 'Indiranagar', bio: 'Hello' }) },
    })
    const user = userEvent.setup()
    renderApp('/profile')
    await user.click(await screen.findByRole('button', { name: 'Edit profile' }))

    expect(screen.getByLabelText('Area')).toHaveAttribute('placeholder', 'e.g. Koramangala')
    await user.type(screen.getByLabelText('Area'), 'Indiranagar')
    await user.type(screen.getByLabelText('Bio'), 'Hello')
    expect(screen.getByText('5/280')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Your profile has been saved.')
    expect(screen.getByText('Indiranagar, Bangalore')).toBeInTheDocument()
    expect(api.calls('PATCH /users/me')[0].body).toEqual({
      display_name: 'Asha',
      handle: 'asha',
      area: 'Indiranagar',
      bio: 'Hello',
    })
  })

  it('shows a taken handle next to the field', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'PATCH /users/me': {
        status: 409,
        body: { detail: 'That handle is taken. Please choose another.', code: 'handle_taken' },
      },
    })
    const user = userEvent.setup()
    renderApp('/profile')
    await user.click(await screen.findByRole('button', { name: 'Edit profile' }))
    const handle = screen.getByLabelText('Handle')
    await user.clear(handle)
    await user.type(handle, 'ravi')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/that handle is taken/i)).toBeInTheDocument()
    expect(handle).toHaveAttribute('aria-invalid', 'true')
  })
})
