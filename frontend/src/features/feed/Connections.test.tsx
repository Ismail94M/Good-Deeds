import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makePost, page, PRIYA, RAHUL } from './testFixtures'

const ME = makeMe({ handle: 'priya', display_name: 'Priya Sharma' })
const MY_SOCIAL = { body: { followers: 2, following: 1, is_following: null, is_me: true } }

describe('ConnectionsPage', () => {
  it('shows my followers with counts in the tabs, and removes one quietly', async () => {
    const api = mockApi({
      ...signedIn(ME),
      'GET /users/priya/social': MY_SOCIAL,
      'GET /me/followers': [
        page([
          { user: RAHUL, following: false },
          { user: { handle: 'kavya', display_name: 'Kavya Nair' }, following: true },
        ]),
        page([{ user: { handle: 'kavya', display_name: 'Kavya Nair' }, following: true }]),
      ],
      'DELETE /me/followers/rahulv': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/me/connections')
    expect(await screen.findByRole('heading', { name: 'Priya Sharma' })).toBeVisible()
    expect(await screen.findByRole('tab', { name: 'Followers · 2' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Following · 1' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Removing a follower is quiet: they aren’t told, and they can follow you again later.',
      ),
    ).toBeInTheDocument()
    await user.click(
      await screen.findByRole('button', { name: 'Remove Rahul Verma from your followers' }),
    )
    await waitFor(() => expect(api.calls('DELETE /me/followers/rahulv')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByText('@rahulv')).not.toBeInTheDocument())
  })

  it('lists who I follow, with Follow/Following toggles', async () => {
    const api = mockApi({
      ...signedIn(ME),
      'GET /users/priya/social': MY_SOCIAL,
      'GET /me/following': page([{ user: RAHUL, following: true }]),
      'DELETE /users/rahulv/follow': {
        body: { followers: 0, following: 3, is_following: false, is_me: false },
      },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/connections?tab=following')
    const button = await screen.findByRole('button', { name: 'Following Rahul Verma' })
    await user.click(button)
    expect(screen.getByRole('button', { name: 'Follow Rahul Verma' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    await waitFor(() => expect(api.calls('DELETE /users/rahulv/follow')).toHaveLength(1))
    await user.click(screen.getByRole('tab', { name: 'Followers · 2' }))
    expect(router.state.location.search).toBe('?tab=followers')
  })
})

describe('SavedPage', () => {
  it('shows the lock note and my saved deeds', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /me/saved': page([makePost({ saved: true })]),
    })
    renderApp('/me/saved')
    expect(await screen.findByRole('heading', { name: 'Saved' })).toBeVisible()
    expect(screen.getByText('Only you can see what you save.')).toBeInTheDocument()
    const card = await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })
    expect(within(card).getByRole('button', { name: 'Saved. Tap to unsave' })).toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    mockApi({ ...signedIn(ME), 'GET /me/saved': page([]) })
    renderApp('/me/saved')
    expect(await screen.findByText('Nothing saved yet')).toBeInTheDocument()
    expect(screen.getByText('Tap the bookmark on a deed to keep it here.')).toBeInTheDocument()
  })
})

describe('Own profile line', () => {
  it('links followers, following and Saved', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/priya/deed-counts': { body: { self_logged: 1, verified: 2 } },
      'GET /users/priya/social': {
        body: { followers: 1, following: 45, is_following: null, is_me: true },
      },
    })
    renderApp('/profile')
    expect(await screen.findByRole('link', { name: '1 follower' })).toHaveAttribute(
      'href',
      '/me/connections?tab=followers',
    )
    expect(screen.getByRole('link', { name: '45 following' })).toHaveAttribute(
      'href',
      '/me/connections?tab=following',
    )
    expect(screen.getByRole('link', { name: 'Saved' })).toHaveAttribute('href', '/me/saved')
    expect(PRIYA.handle).toBe(ME.handle)
  })
})
