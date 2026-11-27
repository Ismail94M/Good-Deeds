/**
 * QA13 (docs/M6-DESIGN.md §6.2, §8 Frontend, D117–D120, PO Q86/Q88): the avatar rules across
 * screens. The default is the same initials on the same colour for one person everywhere; an
 * unavailable account is a silhouette; signed-out pages never ask for picture URLs; a screen's
 * refs go in one batched call (chunks of 100); an image error falls back to the default; and
 * every screen listed in §6.2 renders the shared Avatar.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../../test/utils'
import { AuthContext, type AuthContextValue } from '../../auth/authContext'
import { AHMED, FEED_OPTIONS, makePost, makePublic, page, RAHUL } from '../../feed/testFixtures'
import { Avatar, isUnavailable } from './Avatar'
import { SILHOUETTE_TONE, toneFor } from './DefaultAvatar'
import { MAX_REFS_PER_CALL, resetAvatarBatchForTests } from './useAvatarUrls'

afterEach(() => resetAvatarBatchForTests())

function urls(ref: string) {
  return {
    sm_url: `https://storage.test/${ref}/sm.webp`,
    md_url: `https://storage.test/${ref}/md.webp`,
    lg_url: `https://storage.test/${ref}/lg.webp`,
    expires_at: '2099-01-01T00:00:00Z',
  }
}

/** A P5 handler that signs every ref it is asked for. */
const signAll = (req: { body: unknown }) => {
  const refs = (req.body as { refs: string[] }).refs
  return { body: { items: Object.fromEntries(refs.map((r) => [r, urls(r)])) } }
}

function wrap(children: ReactNode, signedInUser = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const auth = { user: signedInUser ? makeMe() : null } as AuthContextValue
  return render(
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
    </QueryClientProvider>,
  )
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 40))
/** The avatar's colour classes (background and its text colour), without the size classes. */
const tone = (el: Element) =>
  el.className.split(' ').filter((c) => c.startsWith('bg-') || /^text-[a-z]+-\d+$/.test(c))

describe('default avatar: one look per person (D120, Q88)', () => {
  it('the header and a feed card show me with the same initials and colour', async () => {
    const me = makeMe({ handle: 'priya', display_name: 'Priya Sharma', avatar: null })
    mockApi({
      ...signedIn(me),
      'GET /feed/options': { body: FEED_OPTIONS },
      'GET /feed': page([
        makePost({ deed_id: 'd1', doer: { handle: 'priya', display_name: 'Priya Sharma' } }),
      ]),
    })
    renderApp('/')
    const card = await screen.findByRole('article', { name: 'Deed by Priya Sharma' })
    const inCard = card.querySelector('[data-avatar]')!
    const profileLink = screen.getByRole('link', { name: /Profile/ })
    const inHeader = profileLink.querySelector('[data-avatar]')!
    expect(inCard).toHaveTextContent('PS')
    expect(inHeader).toHaveTextContent('PS')
    expect(tone(inCard)).toEqual(tone(inHeader))
    expect(inCard.className).toContain(toneFor('priya'))
  })

  it('the colour follows the handle, not the display name or the size', () => {
    mockApi()
    const { container } = wrap(
      <>
        <Avatar name="Ravi Kumar" handle="ravi" size="xs" />
        <Avatar name="Ravi K." handle="ravi" size="xl" />
        <Avatar user={{ handle: 'ravi', display_name: 'Ravi Kumar' }} size="story" />
      </>,
    )
    const avatars = [...container.querySelectorAll('[data-avatar]')]
    const tones = avatars.map(tone)
    expect(tones[1]).toEqual(tones[0])
    expect(tones[2]).toEqual(tones[0])
    // Five tones, so the mapping is deterministic but not all the same colour.
    const many = ['asha', 'ravi', 'priya', 'ahmedk', 'rahulv', 'sneha', 'kavya'].map(toneFor)
    expect(new Set(many).size).toBeGreaterThan(1)
    expect(toneFor('ravi')).toBe(toneFor('ravi'))
  })
})

describe('unavailable accounts: a silhouette, never a name or a picture', () => {
  it('a null handle or the "Unavailable account" name both count as unavailable', () => {
    expect(isUnavailable({ handle: null, display_name: 'Someone' })).toBe(true)
    expect(isUnavailable({ handle: 'x', display_name: 'Unavailable account' })).toBe(true)
    expect(isUnavailable({ handle: 'x', display_name: 'Xavier' })).toBe(false)
  })

  it('ignores any ref, shows the grey silhouette and asks for nothing', async () => {
    const api = mockApi({ 'POST /media/avatars/urls': signAll })
    const { container } = wrap(
      <>
        <Avatar user={{ handle: 'gone', display_name: 'Unavailable account', avatar: 'ref-1' }} />
        <Avatar user={{ handle: null, display_name: 'Unavailable account', avatar: 'ref-2' }} />
        <Avatar name="Asha" handle="asha" avatar="ref-3" unavailable />
      </>,
    )
    await settle()
    expect(screen.getAllByTestId('avatar-silhouette')).toHaveLength(3)
    for (const el of container.querySelectorAll('[data-avatar]')) {
      expect(el).toHaveAttribute('data-avatar', 'silhouette')
      expect(el.className).toContain(SILHOUETTE_TONE)
      expect(el.textContent).toBe('')
    }
    expect(container.querySelectorAll('img')).toHaveLength(0)
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(0)
  })
})

describe('signed-out pages never ask for picture URLs (D119, Q86)', () => {
  it('the public deed page shows defaults even if a ref slipped into the response', async () => {
    const api = mockApi({
      'GET /public/deeds/d1': {
        body: makePublic({
          doer: { ...AHMED, avatar: 'ref-ahmed' } as typeof AHMED,
          reporter: { ...RAHUL, avatar: 'ref-rahul' } as typeof RAHUL,
        }),
      },
      'POST /media/avatars/urls': signAll,
    })
    const { container } = renderApp('/d/d1')
    const card = await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })
    expect(within(card).getByText('AK')).toBeInTheDocument()
    await settle()
    expect(container.querySelectorAll('[data-avatar] img')).toHaveLength(0)
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(0)
  })

  it('the sign-in and sign-up pages ask for nothing', async () => {
    const api = mockApi()
    renderApp('/login')
    await screen.findByRole('heading', { level: 1 })
    await settle()
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(0)
  })
})

describe('batching (§6.1)', () => {
  it('a feed screen asks for all its refs, deduplicated, in one call', async () => {
    const api = mockApi({
      ...signedIn(makeMe({ avatar: null })),
      'GET /feed/options': { body: FEED_OPTIONS },
      'GET /feed': page([
        makePost({ deed_id: 'd1', doer: { ...AHMED, avatar: 'a1' } }),
        makePost({ deed_id: 'd2', doer: { ...RAHUL, avatar: 'r1' } }),
        makePost({ deed_id: 'd3', doer: { ...AHMED, avatar: 'a1' } }),
        makePost({ deed_id: 'd4', doer: { handle: 'nopic', display_name: 'No Pic' } }),
      ]),
      'POST /media/avatars/urls': signAll,
    })
    const { container } = renderApp('/')
    await screen.findAllByRole('article')
    await waitFor(() => expect(container.querySelectorAll('[data-avatar] img')).toHaveLength(3))
    const calls = api.calls('POST /media/avatars/urls')
    expect(calls).toHaveLength(1)
    expect((calls[0].body as { refs: string[] }).refs.sort()).toEqual(['a1', 'r1'])
  })

  it('splits more than 100 refs into chunks of 100 (avatar_sign_max_refs)', async () => {
    const api = mockApi({ 'POST /media/avatars/urls': signAll })
    const people = Array.from({ length: MAX_REFS_PER_CALL + 1 }, (_, i) => ({
      handle: `p${i}`,
      display_name: `Person ${i}`,
      avatar: `ref-${i}`,
    }))
    const { container } = wrap(
      <>
        {people.map((p) => (
          <Avatar key={p.handle} user={p} size="sm" />
        ))}
      </>,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('img')).toHaveLength(MAX_REFS_PER_CALL + 1),
    )
    const sizes = api
      .calls('POST /media/avatars/urls')
      .map((r) => (r.body as { refs: string[] }).refs.length)
    expect(sizes).toEqual([MAX_REFS_PER_CALL, 1])
  })

  it('a ref already loaded on one screen is not asked for again', async () => {
    const api = mockApi({ 'POST /media/avatars/urls': signAll })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const auth = { user: makeMe() } as AuthContextValue
    const tree = (n: number) => (
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={auth}>
          {Array.from({ length: n }, (_, i) => (
            <Avatar key={i} user={{ handle: 'asha', display_name: 'Asha', avatar: 'a1' }} />
          ))}
        </AuthContext.Provider>
      </QueryClientProvider>
    )
    const { container, rerender } = render(tree(1))
    await waitFor(() => expect(container.querySelectorAll('img')).toHaveLength(1))
    rerender(tree(3))
    await waitFor(() => expect(container.querySelectorAll('img')).toHaveLength(3))
    await settle()
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(1)
  })
})

describe('image errors fall back to the default', () => {
  it('when the refresh says the picture is gone, the initials stay', async () => {
    const api = mockApi({
      'POST /media/avatars/urls': [
        { body: { items: { a1: urls('a1') } } },
        { body: { items: { a1: null } } },
      ],
    })
    const { container } = wrap(
      <Avatar user={{ handle: 'asha', display_name: 'Asha Rao', avatar: 'a1' }} />,
    )
    await waitFor(() => expect(container.querySelectorAll('img')).toHaveLength(1))
    fireEvent.error(container.querySelector('img')!)
    await waitFor(() => expect(api.calls('POST /media/avatars/urls')).toHaveLength(2))
    await settle()
    expect(container.querySelectorAll('img')).toHaveLength(0)
    expect(container.querySelector('[data-avatar]')).toHaveTextContent('AR')
  })

  it('when the refresh call itself fails, the initials stay', async () => {
    const api = mockApi({
      'POST /media/avatars/urls': [
        { body: { items: { a1: urls('a1') } } },
        { status: 500, body: { detail: 'Oops' } },
      ],
    })
    const { container } = wrap(
      <Avatar user={{ handle: 'asha', display_name: 'Asha Rao', avatar: 'a1' }} />,
    )
    await waitFor(() => expect(container.querySelectorAll('img')).toHaveLength(1))
    fireEvent.error(container.querySelector('img')!)
    await waitFor(() => expect(api.calls('POST /media/avatars/urls')).toHaveLength(2))
    await settle()
    expect(container.querySelectorAll('img')).toHaveLength(0)
    expect(container.querySelector('[data-avatar]')).toHaveTextContent('AR')
  })
})

/**
 * §6.2 sweep: every screen that names a person renders the shared `Avatar` (or `AvatarLink`),
 * and nothing draws initials by hand any more. A source check, because rendering ~25 screens
 * with full fixtures would duplicate their own tests.
 */
describe('§6.2 call sites', () => {
  const sources = import.meta.glob<string>(['/src/**/*.tsx', '!/src/**/*.test.tsx'], {
    query: '?raw',
    import: 'default',
    eager: true,
  })
  const SCREENS = [
    'app/Layout.tsx',
    'features/feed/PostCard.tsx',
    'features/feed/CommentsSection.tsx',
    'features/feed/StoriesBar.tsx',
    'features/feed/StoryViewer.tsx',
    'features/feed/ConnectionsPage.tsx',
    'features/profile/PublicProfilePage.tsx',
    'features/profile/ProfilePage.tsx',
    'features/report/DeedForm.tsx',
    'features/scoring/LeaderboardPage.tsx',
    'features/scoring/FeaturedCity.tsx',
    'features/notifications/NotificationsPage.tsx',
    'features/reviews/ReviewsPage.tsx',
    'features/admin/PhotoReportsPage.tsx',
    'features/admin/PhotoReportReviewPage.tsx',
    'features/admin/CommentReportsPage.tsx',
    'features/admin/CommentReportReviewPage.tsx',
    'features/admin/FraudCasesPage.tsx',
    'features/admin/FraudCaseReviewPage.tsx',
    'features/admin/AppealsPage.tsx',
    'features/admin/AppealReviewPage.tsx',
    'features/admin/OverrideRequestsPage.tsx',
    'features/admin/UserScoringAdminPage.tsx',
    'features/admin/AuditLogPage.tsx',
  ]

  it.each(SCREENS)('%s renders the shared Avatar', (file) => {
    const source = sources[`/src/${file}`]
    expect(source, `${file} not found`).toBeDefined()
    expect(source).toMatch(/<Avatar(Link)?\b/)
  })

  it('no screen computes initials itself (only DefaultAvatar does)', () => {
    const offenders = Object.entries(sources)
      .filter(([path, src]) => !path.endsWith('/DefaultAvatar.tsx') && /\binitials\(/.test(src))
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })
})
