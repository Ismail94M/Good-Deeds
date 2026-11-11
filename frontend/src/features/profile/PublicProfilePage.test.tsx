import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderApp } from '../../test/utils'

describe('PublicProfilePage', () => {
  it('shows a public profile without private details', async () => {
    mockApi({
      'GET /users/asha': {
        body: {
          handle: 'asha',
          display_name: 'Asha',
          city: 'Bangalore',
          area: 'Jayanagar',
          bio: 'Kindness first.',
          joined_at: '2026-11-01T10:00:00Z',
        },
      },
    })
    renderApp('/u/asha')
    expect(await screen.findByRole('heading', { name: 'Asha' })).toBeInTheDocument()
    expect(screen.getByText('Jayanagar, Bangalore')).toBeInTheDocument()
    expect(screen.getByText('Kindness first.')).toBeInTheDocument()
    expect(screen.getByText(/joined november 2026/i)).toBeInTheDocument()
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
