/**
 * QA4: profile counts and links (D4, D15, D25, Q22).
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makeOptions } from '../report/testFixtures'

const ASHA_PUBLIC = {
  handle: 'asha',
  display_name: 'Asha',
  city: 'Bangalore',
  area: null,
  bio: null,
  joined_at: '2026-11-01T10:00:00Z',
}

describe('profile counts', () => {
  it('are plain numbers on my own public page (/u/:handle); links only on /profile', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /users/asha': { body: ASHA_PUBLIC },
      'GET /users/asha/deed-counts': { body: { verified: 5 } },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    expect(await within(counts).findByText('5')).toBeVisible()
    expect(within(counts).queryByRole('link')).not.toBeInTheDocument()
  })

  it('Verified Deeds on my profile opens My deeds on Recognised for', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /users/asha/deed-counts': { body: { verified: 5 } },
      'GET /me/deeds': { body: { items: [], next_cursor: null } },
      'GET /deeds/options': { body: makeOptions() },
    })
    const user = userEvent.setup()
    renderApp('/profile')
    await user.click(await screen.findByRole('link', { name: '5 Verified Deeds' }))
    expect(await screen.findByRole('tab', { name: 'Recognised for' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('shows the new self-log count after logging a deed', async () => {
    let logs = 0
    const api = mockApi({
      ...signedIn(makeMe()),
      'GET /users/asha/deed-counts': { body: { verified: 0 } },
      'GET /deeds/options': { body: makeOptions() },
      // M4 D69: my self-log count comes from `total` on my own list.
      'GET /me/self-logs': () => ({ body: { items: [], next_cursor: null, total: logs } }),
      'POST /me/self-logs': () => {
        logs += 1
        return {
          status: 201,
          body: {
            id: 'l1',
            category: 'animals',
            description: 'Fed the stray dogs.',
            occurred_at: new Date().toISOString(),
            has_location: false,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        }
      },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/profile')
    expect(await screen.findByRole('link', { name: '0 Self-log Deeds' })).toBeVisible()
    await user.click(screen.getByRole('link', { name: 'Log a deed' }))
    await user.click(await screen.findByRole('button', { name: 'Animals' }))
    await user.type(
      screen.getByRole('textbox', { name: 'What did you do?' }),
      'Fed the stray dogs.',
    )
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs'))
    await router.navigate('/profile')
    expect(await screen.findByRole('link', { name: '1 Self-log Deeds' })).toBeVisible()
    expect(api.calls('POST /me/self-logs')).toHaveLength(1)
  })
})

describe('profile header and account (Q22, D25)', () => {
  it('shows "@handle" alone under the name (no city or area suffix)', async () => {
    mockApi({
      ...signedIn(makeMe({ area: 'Indiranagar' })),
      'GET /users/asha/deed-counts': { body: { verified: 0 } },
    })
    renderApp('/profile')
    const name = await screen.findByRole('heading', { level: 1, name: 'Asha' })
    const handleLine = name.nextElementSibling as HTMLElement
    expect(handleLine).toHaveTextContent(/^@asha$/)
  })

  it('puts Log out in the Account section with a 44 px target', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /users/asha/deed-counts': { body: { verified: 0 } },
    })
    renderApp('/profile')
    const account = await screen.findByRole('region', { name: 'Account' })
    const logout = within(account).getByRole('button', { name: 'Log out' })
    expect(logout.className).toMatch(/\bmin-h-1[1-3]\b/)
    expect(screen.getAllByRole('button', { name: 'Log out' })).toHaveLength(1)
  })
})
