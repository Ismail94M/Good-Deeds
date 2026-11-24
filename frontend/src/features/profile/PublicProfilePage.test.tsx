import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn, TOKEN } from '../../test/utils'
import { makePost, page } from '../feed/testFixtures'

const ASHA = {
  handle: 'asha',
  display_name: 'Asha',
  city: 'Bangalore',
  area: 'Jayanagar',
  bio: 'Kindness first.',
  joined_at: '2026-11-01T10:00:00Z',
}

/** `GET /scoring/users/asha` (M4 §3.3 #4): tier, badges and titles; never points (D69). */
const ASHA_SCORING = {
  tier: { slug: 'tree', label: 'Tree' },
  badges_total: 3,
  titles_total: 1,
  badges: [
    { slug: 'first_recognised', label: 'First recognition' },
    { slug: 'kind_5', label: '5 deeds' },
    { slug: 'first_witness', label: 'First witness' },
  ],
  titles: [
    {
      label: 'Kindness Hero · Koramangala · November 2026',
      period_month: '2026-11',
      region_name: 'Koramangala',
      place: 1,
      level: 'ward',
    },
  ],
}

describe('PublicProfilePage', () => {
  it('shows a public profile without private details or a city', async () => {
    mockApi({
      'GET /users/asha': { body: ASHA },
      'GET /users/asha/deed-counts': { body: { verified: 31 } },
    })
    renderApp('/u/asha')
    expect(await screen.findByRole('heading', { name: 'Asha' })).toBeInTheDocument()
    expect(screen.getByText('@asha')).toBeInTheDocument()
    expect(screen.queryByText(/Bangalore|Jayanagar/)).not.toBeInTheDocument()
    expect(screen.getByText('Kindness first.')).toBeInTheDocument()
    expect(screen.getByText(/joined november 2026/i)).toBeInTheDocument()
  })

  it('signed out: tier, badges and titles from the public scoring read, never points', async () => {
    mockApi({
      'GET /users/asha': { body: ASHA },
      'GET /users/asha/deed-counts': { body: { verified: 31 } },
      'GET /users/asha/social': {
        body: { followers: 12, following: 45, is_following: null, is_me: false },
      },
      // #4 is public (§3.4 item 4). Even if points leaked into the reply, none are shown.
      'GET /scoring/users/asha': {
        body: { ...ASHA_SCORING, total_points: 987, doer_points: 987, provisional_points: 3 },
      },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    expect(await within(counts).findByText('31')).toBeVisible()
    expect(within(counts).getByText('Verified Deeds')).toBeVisible()
    expect(await within(counts).findByText('12')).toBeVisible()
    expect(await within(counts).findByRole('button', { name: '3 Badges' })).toBeVisible()
    expect(within(counts).getByRole('button', { name: '1 Hero titles' })).toBeVisible()
    expect(within(counts).queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText('Tree')).toHaveTextContent('Tier: Tree')
    expect(document.body).not.toHaveTextContent(/987|points/i)
    expect(
      screen.getByText(/were confirmed in person by someone who saw them\./).closest('p'),
    ).toHaveTextContent('Verified Deeds were confirmed in person by someone who saw them.')
  })

  it('never shows a Self-log Deeds count or its explainer (M4 D69, PO Q55)', async () => {
    mockApi({
      'GET /users/asha': { body: ASHA },
      // Even if an old API still sent the field, the page must not show it.
      'GET /users/asha/deed-counts': { body: { verified: 31, self_logged: 8 } },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    await within(counts).findByText('31')
    expect(screen.queryByText(/self-log/i)).not.toBeInTheDocument()
    expect(within(counts).queryByText('8')).not.toBeInTheDocument()
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
      'GET /users/asha/deed-counts': { body: { verified: 31 } },
      'GET /users/asha/social': social(),
      'GET /feed/users/asha/deeds': page([
        makePost({ doer: { handle: 'asha', display_name: 'Asha' } }),
      ]),
      ...(extra as Record<string, never>),
    })
  }

  it('opened from a link, asks for follow state only once the session is restored', async () => {
    // The server answers this optional-login read as a visitor when no token is sent. Asked
    // before the refresh finished, that reply showed "Follow" to someone already following.
    let releaseRefresh = () => {}
    const refreshed = new Promise<void>((resolve) => (releaseRefresh = resolve))
    const api = setup({
      'POST /auth/refresh': async () => {
        await refreshed
        return { body: TOKEN }
      },
      'GET /users/asha/social': (req: { headers: Headers }) =>
        req.headers.get('Authorization')
          ? { body: { followers: 12, following: 45, is_following: true, is_me: false } }
          : { body: { followers: 12, following: 45, is_following: null, is_me: false } },
    })
    renderApp('/u/asha')
    expect(await screen.findByRole('heading', { name: 'Asha' })).toBeInTheDocument()
    expect(api.calls('GET /users/asha/social')).toHaveLength(0)
    releaseRefresh()
    expect(await screen.findByRole('button', { name: 'Following Asha' })).toBeVisible()
    expect(api.calls('GET /users/asha/social').every((r) => r.headers.has('Authorization'))).toBe(
      true,
    )
  })

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
      'GET /users/asha/deed-counts': { body: { verified: 31 } },
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

describe('PublicProfilePage: tier, badges and titles (M4 F19, D69)', () => {
  function setup(extra: Record<string, unknown> = {}) {
    return mockApi({
      ...signedIn(makeMe({ handle: 'priya', display_name: 'Priya Sharma' })),
      'GET /users/asha': { body: ASHA },
      'GET /users/asha/deed-counts': { body: { verified: 31 } },
      'GET /users/asha/social': {
        body: { followers: 12, following: 45, is_following: false, is_me: false },
      },
      'GET /scoring/users/asha': { body: ASHA_SCORING },
      'GET /feed/users/asha/deeds': page([]),
      ...(extra as Record<string, never>),
    })
  }

  it('shows the five counts and the tier from the API', async () => {
    setup()
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    expect(await within(counts).findByRole('button', { name: '3 Badges' })).toBeVisible()
    expect(within(counts).getByRole('button', { name: '1 Hero titles' })).toBeVisible()
    expect(within(counts).getByText('31')).toBeVisible()
    expect(await within(counts).findByText('12')).toBeVisible()
    expect(within(counts).getByText('45')).toBeVisible()
    expect(screen.getByText('Tree')).toHaveTextContent('Tier: Tree')
  })

  it('opens the badge medals, and shows Hero titles as cards the Hero titles tile jumps to (D75)', async () => {
    setup()
    const user = userEvent.setup()
    renderApp('/u/asha')
    const badges = await screen.findByRole('button', { name: '3 Badges' })
    expect(badges).toHaveAttribute('aria-expanded', 'false')
    await user.click(badges)
    expect(badges).toHaveAttribute('aria-expanded', 'true')
    const list = screen.getByRole('region', { name: 'Badges' })
    expect(within(list).getByText('First recognition')).toBeVisible()
    expect(within(list).getByText('First witness')).toBeVisible()
    await user.click(badges)
    expect(screen.queryByRole('region', { name: 'Badges' })).not.toBeInTheDocument()

    // Titles are always shown (no toggle); the tile moves focus to their heading.
    const titles = screen.getByRole('region', { name: 'Kindness Hero titles' })
    const card = within(titles).getByRole('article')
    expect(card).toHaveAttribute('data-look', 'area')
    expect(card).toHaveTextContent('Area Kindness Hero · #1KoramangalaNovember 2026')
    await user.click(screen.getByRole('button', { name: '1 Hero titles' }))
    expect(screen.getByRole('heading', { name: 'Kindness Hero titles' })).toHaveFocus()
    // Followers and Following stay plain numbers (Q36).
    expect(screen.queryByRole('button', { name: /Followers|Following/ })).not.toBeInTheDocument()
  })

  it('styles City and Area titles differently, most prestigious first, folding extras away (D75)', async () => {
    const title = (o: object) => ({
      label: 'Kindness Hero · Koramangala · November 2026',
      period_month: '2026-11',
      region_name: 'Koramangala',
      place: 1,
      level: 'ward',
      ...o,
    })
    setup({
      'GET /scoring/users/asha': {
        body: {
          ...ASHA_SCORING,
          titles_total: 5,
          titles: [
            title({}),
            title({
              label: 'Top 3 in Bangalore',
              region_name: 'Bangalore',
              level: 'city',
              place: 2,
              period_month: '2026-09',
            }),
            title({
              label: 'Bangalore City Kindness Hero',
              region_name: 'Bangalore',
              level: 'city',
              period_month: '2026-10',
            }),
            title({ label: 'Kindness Hero · Koramangala · August 2026', period_month: '2026-08' }),
            title({
              label: 'Kindness Hero · Indiranagar · July 2026',
              region_name: 'Indiranagar',
              period_month: '2026-07',
            }),
          ],
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/u/asha')
    const titles = await screen.findByRole('region', { name: 'Kindness Hero titles' })
    expect(titles).toHaveTextContent('5 earned')
    const cards = within(titles).getAllByRole('article')
    expect(cards.map((c) => c.dataset.look)).toEqual(['crown', 'podium', 'area'])
    expect(cards[0]).toHaveTextContent(
      'City Kindness Hero · #1Bangalore City Kindness HeroOctober 2026',
    )
    expect(cards[1]).toHaveTextContent('City · #2Top 3 in BangaloreSeptember 2026')
    expect(cards[2]).toHaveTextContent('Area Kindness Hero · #1KoramangalaNovember 2026')
    expect(within(titles).queryByText(/Indiranagar/)).not.toBeInTheDocument()
    const more = within(titles).getByRole('button', { name: 'Show all 5 titles' })
    await user.click(more)
    expect(more).toHaveAttribute('aria-expanded', 'true')
    expect(within(titles).getByText('Area Kindness Hero · #1 · Indiranagar')).toBeVisible()
    expect(within(titles).getByText('July 2026')).toBeVisible()
    await user.click(within(titles).getByRole('button', { name: 'Show fewer titles' }))
    expect(within(titles).queryByText(/Indiranagar/)).not.toBeInTheDocument()
  })

  it('never shows points or a self-log count on another person’s profile', async () => {
    // A response carrying owner-only fields by mistake must still render no points.
    const api = setup({
      'GET /scoring/users/asha': {
        body: { ...ASHA_SCORING, doer_points: 987, reporter_points: 654, provisional_points: 3 },
      },
      'GET /users/asha/deed-counts': { body: { verified: 31, self_logged: 8 } },
    })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    await within(counts).findByRole('button', { name: '3 Badges' })
    const main = screen.getByRole('main')
    expect(within(main).queryByText(/points?/i)).not.toBeInTheDocument()
    expect(within(main).queryByText(/987|654/)).not.toBeInTheDocument()
    expect(within(main).queryByText(/self-log/i)).not.toBeInTheDocument()
    expect(within(counts).queryByText('8')).not.toBeInTheDocument()
    // The owner-only endpoints are never called for someone else's profile.
    expect(api.calls('GET /scoring/me')).toHaveLength(0)
    expect(api.calls('GET /me/self-logs')).toHaveLength(0)
  })

  it('shows dashes for badges and titles when scoring can’t load', async () => {
    setup({ 'GET /scoring/users/asha': { status: 404, body: { detail: 'x', code: 'not_found' } } })
    renderApp('/u/asha')
    const counts = await screen.findByRole('group', { name: 'Counts' })
    await waitFor(() => expect(within(counts).getAllByText('–')).toHaveLength(2))
    expect(within(counts).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByText(/Tier:/)).not.toBeInTheDocument()
  })
})
