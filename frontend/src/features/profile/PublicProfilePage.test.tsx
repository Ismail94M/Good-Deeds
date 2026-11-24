import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makePost, page } from '../feed/testFixtures'

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
    const counts = await screen.findByRole('group', { name: 'Counts' })
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

describe('PublicProfilePage: follows (F13)', () => {
  const social = (overrides = {}) => ({
    body: { followers: 12, following: 45, is_following: false, is_me: false, ...overrides },
  })

  function setup(extra: Record<string, unknown> = {}) {
    return mockApi({
      ...signedIn(makeMe({ handle: 'priya', display_name: 'Priya Sharma' })),
      'GET /users/asha': { body: ASHA },
      'GET /users/asha/deed-counts': { body: { self_logged: 8, verified: 31 } },
      'GET /users/asha/social': social(),
      'GET /feed/users/asha/deeds': page([
        makePost({ doer: { handle: 'asha', display_name: 'Asha' } }),
      ]),
      ...(extra as Record<string, never>),
    })
  }

  it('shows four plain counts, the deeds heading and the deeds (Q36)', async () => {
    setup()
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    expect(await within(counts).findByText('12')).toBeVisible()
    expect(within(counts).getByText('45')).toBeVisible()
    expect(within(counts).getByText('Followers')).toBeVisible()
    expect(within(counts).getByText('Following')).toBeVisible()
    expect(within(counts).queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Asha’s deeds' })).toBeVisible()
    expect(await screen.findByRole('article', { name: 'Deed by Asha' })).toBeInTheDocument()
  })

  it('Follow toggles the button and the follower count optimistically', async () => {
    const api = setup({
      'PUT /users/asha/follow': social({ followers: 13, is_following: true }),
    })
    const user = userEvent.setup()
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    await within(counts).findByText('12')
    const follow = screen.getAllByRole('button', { name: 'Follow Asha' })[0]
    expect(follow).toHaveAttribute('aria-pressed', 'false')
    await user.click(follow)
    expect(within(counts).getByText('13')).toBeVisible()
    expect(screen.getAllByRole('button', { name: 'Following Asha' })[0]).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await waitFor(() => expect(api.calls('PUT /users/asha/follow')).toHaveLength(1))
  })

  it('rolls the follow back on error', async () => {
    setup({
      'PUT /users/asha/follow': { status: 409, body: { detail: 'x', code: 'following_limit' } },
    })
    const user = userEvent.setup()
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    await within(counts).findByText('12')
    await user.click(screen.getAllByRole('button', { name: 'Follow Asha' })[0])
    await waitFor(() => expect(within(counts).getByText('12')).toBeVisible())
    expect(await screen.findByText('That didn’t work. Please try again.')).toBeInTheDocument()
  })

  it('has no Follow button on my own profile', async () => {
    setup({
      'GET /users/asha/social': social({ is_me: true, is_following: null }),
      'GET /feed/users/asha/deeds': page([
        makePost({ my_role: 'doer', doer: { handle: 'asha', display_name: 'Asha' } }),
      ]),
    })
    renderApp('/u/asha')
    await within(await screen.findByRole('group', { name: 'Counts' })).findByText('12')
    await screen.findByRole('article', { name: 'Deed by Asha' })
    expect(screen.queryByRole('button', { name: /Follow/ })).not.toBeInTheDocument()
  })

  it('signed out: counts and Join to follow, no deeds list', async () => {
    const api = mockApi({
      'GET /users/asha': { body: ASHA },
      'GET /users/asha/deed-counts': { body: { self_logged: 8, verified: 31 } },
      'GET /users/asha/social': social({ is_following: null }),
    })
    renderApp('/u/asha')
    expect(await screen.findByRole('link', { name: 'Join to follow' })).toHaveAttribute(
      'href',
      `/signup?next=${encodeURIComponent('/u/asha')}`,
    )
    expect(api.calls('GET /feed/users/asha/deeds')).toHaveLength(0)
  })
})
