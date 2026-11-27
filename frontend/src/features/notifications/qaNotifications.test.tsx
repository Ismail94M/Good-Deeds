/**
 * QA13 (docs/M6-DESIGN.md §3.2 N1–N7, §6.3, §8 Frontend, D101, D108, D113, D114, PO Q92/Q93):
 * the bell's 9 / 9+ boundary and its polling interval from `unread_poll_seconds` (with the 15 s
 * floor); grouped rows rendered as the server wrote them with at most two faces; "This deed is
 * no longer available" rows with no names and no tap; rows without a link; tap → read + open;
 * mark all; the 409 `newer_unread_exists` refetch; the row menu offering only the `can_mute` keys
 * present; locked categories; and a scan of everything rendered for review/fraud words (D101).
 */

import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn, type MockRequest } from '../../test/utils'
import { resetAvatarBatchForTests } from '../media/avatar/useAvatarUrls'
import type { NotificationItem, Preferences } from './notificationsApi'

afterEach(() => resetAvatarBatchForTests())

const RAVI = { handle: 'ravi', display_name: 'Ravi Kumar', avatar: null }
const SNEHA = { handle: 'sneha', display_name: 'Sneha Rao', avatar: null }
const KAVYA = { handle: 'kavya', display_name: 'Kavya Nair', avatar: null }

let seq = 0
function item(overrides: Partial<NotificationItem> = {}): NotificationItem {
  seq += 1
  return {
    id: `n-${seq}`,
    kind: 'like',
    category: 'activity',
    created_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
    updated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    read: false,
    text: 'Ravi, Sneha and 4 others liked the deed about you.',
    actors: [RAVI, SNEHA, KAVYA],
    actor_count: 6,
    link: { kind: 'deed', target: 'deed-1' },
    can_mute: { person: 'ravi', deed: 'deed-1' },
    ...overrides,
  }
}

function list(items: NotificationItem[], extra: Record<string, unknown> = {}) {
  return mockApi({
    ...signedIn(makeMe()),
    'GET /notifications/unread-count': {
      body: { unread: items.filter((i) => !i.read).length, new_since_seen: 0 },
    },
    'GET /notifications': { body: { items, next_cursor: null } },
    'POST /notifications/seen': { status: 204 },
    'POST /media/avatars/urls': { body: { items: {} } },
    ...(extra as Record<string, never>),
  })
}

const rowOf = (text: string | RegExp) => {
  const el = screen.getByText(text)
  return el.closest('li') as HTMLElement
}

describe('bell count boundaries', () => {
  it.each([
    [1, '1', 'Notifications, 1 new'],
    [9, '9', 'Notifications, 9 new'],
    [10, '9+', 'Notifications, 10 new'],
    [250, '9+', 'Notifications, 250 new'],
  ])('%i new → shows "%s"', async (n, shown, label) => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /notifications/unread-count': { body: { unread: n, new_since_seen: n } },
    })
    renderApp('/')
    const bell = await screen.findByRole('link', { name: label }, { timeout: 4000 })
    expect(within(bell).getByTestId('bell-count').textContent).toBe(shown)
  })

  it('counts new_since_seen, not all unread', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /notifications/unread-count': { body: { unread: 14, new_since_seen: 2 } },
    })
    renderApp('/')
    expect(
      await screen.findByRole('link', { name: 'Notifications, 2 new' }, { timeout: 4000 }),
    ).toBeInTheDocument()
  })
})

describe('bell polling (D114)', () => {
  async function pollsAfter(pollSeconds: number | undefined) {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const api = mockApi({
      ...signedIn(makeMe()),
      'GET /notifications/unread-count': {
        body: { unread: 0, new_since_seen: 0, unread_poll_seconds: pollSeconds },
      },
    })
    renderApp('/')
    await screen.findByRole('link', { name: 'Notifications' }, { timeout: 4000 })
    const count = () => api.calls('GET /notifications/unread-count').length
    await waitFor(() => expect(count()).toBe(1))
    return { api, count }
  }

  it('uses the server’s unread_poll_seconds', async () => {
    const { count } = await pollsAfter(20)
    await act(() => vi.advanceTimersByTimeAsync(19_000))
    expect(count()).toBe(1)
    await act(() => vi.advanceTimersByTimeAsync(1_500))
    expect(count()).toBe(2)
    await act(() => vi.advanceTimersByTimeAsync(20_000))
    expect(count()).toBe(3)
  })

  it('never polls faster than every 15 s, whatever the server sends', async () => {
    const { count } = await pollsAfter(5)
    await act(() => vi.advanceTimersByTimeAsync(14_000))
    expect(count()).toBe(1)
    await act(() => vi.advanceTimersByTimeAsync(1_500))
    expect(count()).toBe(2)
  })

  it('falls back to every minute when the server sends no interval', async () => {
    const { count } = await pollsAfter(undefined)
    await act(() => vi.advanceTimersByTimeAsync(59_000))
    expect(count()).toBe(1)
    await act(() => vi.advanceTimersByTimeAsync(1_500))
    expect(count()).toBe(2)
  })
})

describe('rows', () => {
  it('a grouped row shows the server’s text and at most two faces', async () => {
    list([item()])
    renderApp('/notifications')
    await screen.findByText('Ravi, Sneha and 4 others liked the deed about you.')
    const row = rowOf('Ravi, Sneha and 4 others liked the deed about you.')
    // Three actors come back, but only the newest two are stacked.
    expect(row.querySelectorAll('[data-avatar]')).toHaveLength(2)
    expect(row).toHaveTextContent('RK')
    expect(row).toHaveTextContent('SR')
    expect(row).not.toHaveTextContent('KN')
    // The client never rebuilds the sentence from the actors.
    expect(row).not.toHaveTextContent('Kavya')
  })

  it('"This deed is no longer available": no names, no faces, not tappable', async () => {
    const api = list([
      item({
        kind: 'recognised',
        category: 'about_you',
        text: 'This deed is no longer available',
        actors: [],
        actor_count: 0,
        link: null,
        can_mute: {},
      }),
    ])
    const user = userEvent.setup()
    const { router } = renderApp('/notifications')
    await screen.findByText('This deed is no longer available')
    const row = rowOf('This deed is no longer available')
    expect(within(row).queryByRole('link')).toBeNull()
    expect(row.querySelectorAll('[data-avatar]')).toHaveLength(0)
    expect(row).not.toHaveTextContent(/Ravi|Sneha|Kavya|Unavailable account/)
    await user.click(screen.getByText('This deed is no longer available'))
    expect(router.state.location.pathname).toBe('/notifications')
    expect(api.requests.filter((r) => /^\/notifications\/[^/]+\/read$/.test(r.path))).toHaveLength(
      0,
    )
  })

  it('a row with no link is plain text; it can still be marked read from the menu', async () => {
    const row = item({
      id: 'pw',
      kind: 'password_changed',
      category: 'account',
      text: 'Your password was changed. If this wasn’t you, contact support.',
      actors: [],
      actor_count: 0,
      link: null,
      can_mute: {},
    })
    const api = list([row], { 'POST /notifications/pw/read': { status: 204 } })
    const user = userEvent.setup()
    const { router } = renderApp('/notifications')
    const text = await screen.findByText(/Your password was changed/)
    expect(text.closest('a')).toBeNull()
    await user.click(text)
    expect(router.state.location.pathname).toBe('/notifications')
    expect(api.calls('POST /notifications/pw/read')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'More options for this notification' }))
    const sheet = screen.getByRole('dialog', { name: 'Notification options' })
    expect(within(sheet).queryByRole('button', { name: /Mute|Turn off/ })).toBeNull()
    await user.click(within(sheet).getByRole('button', { name: 'Mark as read' }))
    await waitFor(() => expect(api.calls('POST /notifications/pw/read')).toHaveLength(1))
  })

  it('tapping an unread row marks it read and opens it; a read row is not marked again', async () => {
    const unread = item({ id: 'u1', link: { kind: 'profile', target: 'ravi' } })
    const read = item({
      id: 'r1',
      read: true,
      kind: 'follow',
      text: 'Kavya started following you.',
      actors: [KAVYA],
      actor_count: 1,
      link: { kind: 'connections', target: 'followers' },
      can_mute: { person: 'kavya' },
    })
    const api = list([unread, read], {
      'POST /notifications/u1/read': { status: 204 },
      'POST /notifications/r1/read': { status: 204 },
      'GET /users/ravi': { status: 404, body: { detail: 'Not found', code: 'not_found' } },
      'GET /me/followers': { body: { items: [], next_cursor: null } },
      'GET /users/asha/social': {
        body: { followers: 0, following: 0, following_them: false, follows_you: false },
      },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/notifications')
    await user.click(await screen.findByRole('link', { name: /Kavya started following you/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/connections'))
    expect(router.state.location.search).toBe('?tab=followers')
    expect(api.calls('POST /notifications/r1/read')).toHaveLength(0)

    await act(() => router.navigate('/notifications'))
    await user.click(await screen.findByRole('link', { name: /liked the deed about you/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/u/ravi'))
    expect(api.calls('POST /notifications/u1/read')).toHaveLength(1)
  })

  it('"Mark all as read" shows only while something is unread', async () => {
    list([item({ read: true })], {
      'GET /notifications/unread-count': { body: { unread: 0, new_since_seen: 0 } },
    })
    renderApp('/notifications')
    await screen.findByText(/liked the deed about you/)
    expect(screen.queryByRole('button', { name: 'Mark all as read' })).toBeNull()
  })

  it('a 409 newer_unread_exists reloads the list so the row shows the server’s state', async () => {
    const row = item({ id: 'old', read: true })
    const api = list([row], {
      'POST /notifications/old/unread': {
        status: 409,
        body: { detail: 'Newer', code: 'newer_unread_exists' },
      },
    })
    const user = userEvent.setup()
    renderApp('/notifications')
    await screen.findByText(/liked the deed about you/)
    const before = api.calls('GET /notifications').length
    await user.click(screen.getByRole('button', { name: 'More options for this notification' }))
    await user.click(screen.getByRole('button', { name: 'Mark as unread' }))
    expect(
      await screen.findByText('There’s already a newer unread notification like this one.'),
    ).toBeInTheDocument()
    await waitFor(() => expect(api.calls('GET /notifications').length).toBeGreaterThan(before))
    // The optimistic "unread" is rolled back: the row is read, as the server says.
    await waitFor(() => expect(screen.queryAllByTestId('unread-dot')).toHaveLength(0))
  })
})

describe('row menu: only the can_mute keys that are present (D113)', () => {
  async function openMenu(canMute: NotificationItem['can_mute'], actors = [RAVI, SNEHA]) {
    list([item({ can_mute: canMute, actors })])
    const user = userEvent.setup()
    renderApp('/notifications')
    await screen.findByText(/liked the deed about you/)
    await user.click(screen.getByRole('button', { name: 'More options for this notification' }))
    return screen.getByRole('dialog', { name: 'Notification options' })
  }

  it('person only → "Mute Ravi", no deed option', async () => {
    const sheet = await openMenu({ person: 'ravi' })
    expect(within(sheet).getByRole('button', { name: 'Mute Ravi' })).toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: /this deed/ })).toBeNull()
  })

  it('deed only → the deed option, no one to mute', async () => {
    const sheet = await openMenu({ deed: 'deed-1' })
    expect(
      within(sheet).getByRole('button', { name: 'Turn off notifications for this deed' }),
    ).toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: /^Mute/ })).toBeNull()
  })

  it('neither → only read/unread and Cancel', async () => {
    const sheet = await openMenu({})
    const names = within(sheet)
      .getAllByRole('button')
      .map((b) => b.textContent?.trim())
    expect(names).toEqual(['Mark as read', 'Cancel'])
  })

  it('a person not among the shown actors is named by handle', async () => {
    const sheet = await openMenu({ person: 'meera' })
    expect(within(sheet).getByRole('button', { name: 'Mute @meera' })).toBeInTheDocument()
  })
})

describe('settings: locked categories', () => {
  const PREFS: Preferences = {
    categories: [
      { id: 'activity', label: 'Activity', description: 'Likes', on: true, locked: false },
      {
        id: 'account',
        label: 'Account & safety',
        description: 'Moderation',
        on: true,
        locked: true,
      },
      { id: 'reviews', label: 'Reviews', description: 'Decisions', on: true, locked: true },
    ],
    mutes: { people: [], deeds: [] },
  }

  it('a locked switch can’t be pressed and sends nothing', async () => {
    const api = list([], { 'GET /notifications/preferences': { body: PREFS } })
    const user = userEvent.setup()
    renderApp('/settings/notifications')
    const reviews = await screen.findByRole('switch', { name: 'Reviews' })
    expect(reviews).toBeDisabled()
    await user.click(reviews)
    await user.click(screen.getByRole('switch', { name: 'Account & safety' }))
    expect(api.calls('PUT /notifications/preferences')).toHaveLength(0)
    expect(reviews).toHaveAttribute('aria-checked', 'true')
  })

  it('a 409 category_locked from the server is explained, and the switch stays', async () => {
    list([], {
      'GET /notifications/preferences': { body: PREFS },
      'PUT /notifications/preferences': {
        status: 409,
        body: { detail: 'Locked', code: 'category_locked' },
      },
    })
    const user = userEvent.setup()
    renderApp('/settings/notifications')
    await user.click(await screen.findByRole('switch', { name: 'Activity' }))
    expect(await screen.findByText('This one is always on.')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Activity' })).toHaveAttribute('aria-checked', 'true')
  })
})

describe('no review or fraud wording anywhere (D101)', () => {
  const BANNED = /\b(fraud\w*|flag(s|ged)?|cases?|ring|rings|detector\w*|suspicious|collu\w*)\b/i

  const REVIEW_ROWS = [
    'There’s an update for you in Reviews.',
    'We’d like to hear from you about some recent deeds. Please reply in Reviews.',
    'A reminder: please reply in Reviews by 30 Nov.',
    'We’ve finished looking at your recent deeds. No action needed.',
  ].map((text) =>
    item({
      kind: 'review_update',
      category: 'reviews',
      text,
      actors: [],
      actor_count: 0,
      link: { kind: 'reviews', target: null },
      can_mute: {},
    }),
  )

  it('the list, its row menus and the empty state', async () => {
    list([...REVIEW_ROWS, item()])
    const user = userEvent.setup()
    renderApp('/notifications')
    await screen.findByText('There’s an update for you in Reviews.')
    expect(document.body.textContent).not.toMatch(BANNED)
    // Accessible names too (labels aren't in textContent).
    for (const el of document.querySelectorAll('[aria-label]')) {
      expect(el.getAttribute('aria-label')).not.toMatch(BANNED)
    }
    const menus = screen.getAllByRole('button', { name: 'More options for this notification' })
    await user.click(menus[0])
    expect(screen.getByRole('dialog', { name: 'Notification options' }).textContent).not.toMatch(
      BANNED,
    )
    // Review rows open /me/reviews.
    expect(
      screen.getByRole('link', { name: /There’s an update for you in Reviews/ }),
    ).toHaveAttribute('href', '/me/reviews')
  })

  it('the settings page, including the locked Reviews note', async () => {
    list([], {
      'GET /notifications/preferences': {
        body: {
          categories: [
            {
              id: 'reviews',
              label: 'Reviews',
              description: 'Questions and decisions about your deeds',
              on: true,
              locked: true,
            },
          ],
          mutes: { people: [], deeds: [] },
        },
      },
    })
    renderApp('/settings/notifications')
    await screen.findByRole('switch', { name: 'Reviews' })
    expect(document.body.textContent).not.toMatch(BANNED)
  })

  it('the empty Unread tab', async () => {
    list([])
    renderApp('/notifications?tab=unread')
    await screen.findByText('You’re all caught up')
    expect(document.body.textContent).not.toMatch(BANNED)
  })
})

describe('paging', () => {
  it('"Show more" asks for the next cursor and appends', async () => {
    const first = item({ text: 'First page row.' })
    const second = item({ text: 'Second page row.' })
    const api = list([], {
      'GET /notifications': (req: MockRequest) => ({
        body:
          req.query.get('cursor') === 'c2'
            ? { items: [second], next_cursor: null }
            : { items: [first], next_cursor: 'c2' },
      }),
    })
    const user = userEvent.setup()
    renderApp('/notifications')
    await screen.findByText('First page row.')
    const more = screen.queryByRole('button', { name: 'Show more' })
    if (more) await user.click(more)
    expect(await screen.findByText('Second page row.')).toBeInTheDocument()
    expect(screen.getByText('First page row.')).toBeInTheDocument()
    expect(api.calls('GET /notifications').some((r) => r.query.get('cursor') === 'c2')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull()
  })
})
