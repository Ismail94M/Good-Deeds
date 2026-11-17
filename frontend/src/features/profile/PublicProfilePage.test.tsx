import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderApp } from '../../test/utils'

const ASHA = {
  handle: 'asha',
  display_name: 'Asha',
  city: 'Bangalore',
  area: 'Jayanagar',
  bio: 'Kindness first.',
  joined_at: '2026-11-01T10:00:00Z',
}

describe('PublicProfilePage', () => {
  it('shows a public profile without private details or a city', async () => {
    mockApi({
      'GET /users/asha': { body: ASHA },
      'GET /users/asha/deed-counts': { body: { self_logged: 8, verified: 31 } },
    })
    renderApp('/u/asha')
    expect(await screen.findByRole('heading', { name: 'Asha' })).toBeInTheDocument()
    expect(screen.getByText('@asha')).toBeInTheDocument()
    expect(screen.queryByText(/Bangalore|Jayanagar/)).not.toBeInTheDocument()
    expect(screen.getByText('Kindness first.')).toBeInTheDocument()
    expect(screen.getByText(/joined november 2026/i)).toBeInTheDocument()
  })

  it('shows the counts as plain numbers with an explanation', async () => {
    mockApi({
      'GET /users/asha': { body: ASHA },
      'GET /users/asha/deed-counts': { body: { self_logged: 8, verified: 31 } },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Deed counts' })
    expect(await within(counts).findByText('8')).toBeVisible()
    expect(within(counts).getByText('31')).toBeVisible()
    expect(within(counts).getByText('Self-log Deeds')).toBeVisible()
    expect(within(counts).getByText('Verified Deeds')).toBeVisible()
    expect(within(counts).queryByRole('link')).not.toBeInTheDocument()
    expect(
      screen.getByText(/were confirmed in person by someone who saw them\./).closest('p'),
    ).toHaveTextContent(
      'Verified Deeds were confirmed in person by someone who saw them. Self-logs are private notes: only the number is shown.',
    )
  })

  it('shows a not-found state', async () => {
    mockApi({
      'GET /users/nobody': {
        status: 404,
        body: { detail: "We couldn't find that person.", code: 'user_not_found' },
      },
    })
    renderApp('/u/nobody')
    expect(await screen.findByRole('heading', { name: 'Profile not found' })).toBeInTheDocument()
  })
})
