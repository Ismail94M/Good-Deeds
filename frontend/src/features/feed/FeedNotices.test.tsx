import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { AHMED, FEED_OPTIONS, page, RAHUL } from './testFixtures'

const ME = makeMe({ handle: 'priya', display_name: 'Priya Sharma' })

function setup(handlers: Record<string, unknown> = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /feed/options': { body: FEED_OPTIONS },
    'GET /feed': page([]),
    'GET /feed/me/notices': { body: [] },
    'GET /feed/me/comment-notices': { body: [] },
    'POST /feed/me/notices/public_feed_intro/seen': { status: 204 },
    ...(handlers as Record<string, never>),
  })
}

const INTRO =
  'Deeds people record about you now appear in the public feed with your name and the area. You can hide any of them from the deed’s page.'

describe('Public-feed notice (Q41)', () => {
  it('shows only when the server lists the key, and OK marks it seen', async () => {
    const api = setup({ 'GET /feed/me/notices': { body: ['public_feed_intro'] } })
    const user = userEvent.setup()
    renderApp('/')
    const card = await screen.findByRole('region', { name: 'About the public feed' })
    expect(card).toHaveTextContent(INTRO)
    await user.click(within(card).getByRole('button', { name: 'OK' }))
    expect(screen.queryByText(INTRO)).not.toBeInTheDocument()
    await waitFor(() =>
      expect(api.calls('POST /feed/me/notices/public_feed_intro/seen')).toHaveLength(1),
    )
  })

  it('See my deeds marks it seen and opens My deeds', async () => {
    const api = setup({ 'GET /feed/me/notices': { body: ['public_feed_intro'] } })
    const user = userEvent.setup()
    const { router } = renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'See my deeds' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/deeds'))
    await waitFor(() =>
      expect(api.calls('POST /feed/me/notices/public_feed_intro/seen')).toHaveLength(1),
    )
  })

  it('is absent when the key is not listed', async () => {
    setup()
    renderApp('/')
    await screen.findByText('No deeds yet')
    expect(screen.queryByText(INTRO)).not.toBeInTheDocument()
  })
})

describe('Comment-removed notices (§3.4a)', () => {
  it('shows one card per removed comment, oldest first; OK marks it seen', async () => {
    const api = setup({
      'GET /feed/me/comment-notices': {
        body: [
          {
            comment_id: 'c2',
            deed_id: 'd2',
            doer: RAHUL,
            excerpt: null,
            removed_at: '2026-11-18T10:00:00Z',
          },
          {
            comment_id: 'c1',
            deed_id: 'd1',
            doer: AHMED,
            excerpt: 'You are the worst',
            removed_at: '2026-11-17T10:00:00Z',
          },
        ],
      },
      'POST /feed/me/comment-notices/c1/seen': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/')
    const headings = await screen.findAllByRole('heading', { name: /deed was removed/ })
    expect(headings.map((h) => h.textContent)).toEqual([
      'Your comment on Ahmed Khan’s deed was removed',
      'Your comment on Rahul Verma’s deed was removed',
    ])
    const first = screen.getByRole('region', {
      name: 'Your comment on Ahmed Khan’s deed was removed',
    })
    expect(first).toHaveTextContent('It broke our community guidelines. “You are the worst…”')
    expect(
      within(first).getByRole('link', { name: 'Read the community guidelines' }),
    ).toHaveAttribute('href', '/community-guidelines')
    await user.click(within(first).getByRole('button', { name: 'OK' }))
    expect(screen.getAllByRole('heading', { name: /deed was removed/ })).toHaveLength(1)
    await waitFor(() => expect(api.calls('POST /feed/me/comment-notices/c1/seen')).toHaveLength(1))
  })
})

describe('CommunityGuidelinesPage', () => {
  it('shows the draft guidelines', async () => {
    mockApi()
    renderApp('/community-guidelines')
    expect(await screen.findByRole('heading', { name: 'Community guidelines' })).toBeVisible()
    expect(screen.getByRole('note')).toHaveTextContent('Draft — PO to approve')
    expect(screen.getByRole('heading', { name: '1. Be kind' })).toBeInTheDocument()
  })
})
