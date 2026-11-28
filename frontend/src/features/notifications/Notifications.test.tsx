/**
 * M6 F28 (docs/M6-DESIGN.md §3.2 N1–N7, §6.3, D108, D113–D114): the header bell with its
 * "new since seen" count, the notifications page (server texts, grouped rows with stacked
 * avatars, tabs, tap-through that marks read, mark all, the row menu with read/unread and
 * mutes, empty and error states) and the settings page (switches, locked categories, unmute).
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { resetAvatarBatchForTests } from '../media/avatar/useAvatarUrls'
import { badgeText } from './NotificationBell'
import type { NotificationItem, Preferences } from './notificationsApi'
import { linkHref, relativeTime } from './notificationText'

afterEach(() => resetAvatarBatchForTests())

const RAVI = { handle: 'ravi', display_name: 'Ravi Kumar', avatar: 'ref-ravi' }
const SNEHA = { handle: 'sneha', display_name: 'Sneha Rao', avatar: null }

function makeItem(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: 'n-1',
    kind: 'like',
    category: 'activity',
    created_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
    updated_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    read: false,
    text: 'Ravi, Sneha and 4 others liked the deed about you.',
    actors: [RAVI, SNEHA],
    actor_count: 6,
    link: { kind: 'deed', target: 'deed-1' },
    can_mute: { person: 'ravi', deed: 'deed-1' },
    ...overrides,
  }
}

const READ_ROW = makeItem({
  id: 'n-2',
  kind: 'badge_awarded',
  category: 'account',
  read: true,
  text: 'You earned the First Witness badge.',
  actors: [],
  actor_count: 0,
  link: { kind: 'badges', target: 'asha' },
  can_mute: {},
})

const GONE_ROW = makeItem({
  id: 'n-3',
  kind: 'recognised',
  category: 'about_you',
  read: true,
  text: 'This deed is no longer available',
  actors: [{ handle: null, display_name: 'Unavailable account', avatar: null }],
  actor_count: 1,
  link: null,
  can_mute: {},
})

function prefs(overrides: Partial<Preferences> = {}): Preferences {
  return {
    categories: [
      {
        id: 'about_you',
        label: 'About you',
        description: 'When someone recognises you',
        on: true,
        locked: false,
      },
      {
        id: 'network',
        label: 'Your network',
        description: 'Good deeds by people you follow',
        on: true,
        locked: false,
      },
      {
        id: 'activity',
        label: 'Activity',
        description: 'Likes, comments and new followers',
        on: false,
        locked: false,
      },
      {
        id: 'account',
        label: 'Account & safety',
        description: 'Moderation, badges and titles',
        on: true,
        locked: true,
      },
      {
        id: 'reviews',
        label: 'Reviews',
        description: 'Questions and decisions about your deeds',
        on: true,
        locked: true,
      },
    ],
    mutes: {
      people: [SNEHA],
      deeds: [{ deed_id: 'deed-9', title_text: 'Ravi helped someone cross the road' }],
    },
    ...overrides,
  }
}

function handlers(start: NotificationItem[] = [makeItem(), READ_ROW, GONE_ROW]) {
  // A tiny stateful server: seen clears the bell, read/read-all change the rows.
  let items = start.map((item) => ({ ...item }))
  let seen = false
  const setRead = (id: string | null, read: boolean) => {
    items = items.map((item) => (id === null || item.id === id ? { ...item, read } : item))
    return { status: 204 }
  }
  return {
    ...signedIn(makeMe()),
    'GET /notifications/unread-count': () => ({
      body: {
        unread: items.filter((i) => !i.read).length,
        new_since_seen: seen ? 0 : 3,
      },
    }),
    'GET /notifications': (req: { query: URLSearchParams }) => ({
      body: {
        items: req.query.get('unread') === 'true' ? items.filter((i) => !i.read) : items,
        next_cursor: null,
      },
    }),
    'POST /notifications/seen': () => {
      seen = true
      return { status: 204 }
    },
    'POST /notifications/read-all': () => setRead(null, true),
    'POST /notifications/n-1/read': () => setRead('n-1', true),
    'POST /notifications/n-2/unread': () => setRead('n-2', false),
    'POST /media/avatars/urls': { body: { items: {} } },
  }
}

describe('notification bell', () => {
  it('shows the new count in the header and links to the page', async () => {
    mockApi(handlers())
    renderApp('/')
    const bell = await screen.findByRole(
      'link',
      { name: 'Notifications, 3 new' },
      { timeout: 4000 },
    )
    expect(within(bell).getByTestId('bell-count')).toHaveTextContent('3')
    expect(bell).toHaveAttribute('href', '/notifications')
  })

  it('caps the badge at 9+ and hides it at zero', async () => {
    expect(badgeText(4)).toBe('4')
    expect(badgeText(12)).toBe('9+')
    mockApi({
      ...handlers(),
      'GET /notifications/unread-count': { body: { unread: 0, new_since_seen: 0 } },
    })
    renderApp('/')
    const bell = await screen.findByRole('link', { name: 'Notifications' })
    expect(within(bell).queryByTestId('bell-count')).toBeNull()
  })

  it('is not shown when signed out', async () => {
    const api = mockApi()
    renderApp('/login')
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByRole('link', { name: /^Notifications/ })).toBeNull()
    expect(api.calls('GET /notifications/unread-count')).toHaveLength(0)
  })
})

describe('notifications page', () => {
  it('lists server texts, newest first, marks the page seen and clears the bell', async () => {
    const api = mockApi(handlers())
    renderApp('/notifications')
    expect(
      await screen.findByText('Ravi, Sneha and 4 others liked the deed about you.'),
    ).toBeInTheDocument()
    expect(screen.getByText('You earned the First Witness badge.')).toBeInTheDocument()
    expect(screen.getByText('This deed is no longer available')).toBeInTheDocument()
    await waitFor(() => expect(api.calls('POST /notifications/seen')).toHaveLength(1))
    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'Notifications' })).toBeInTheDocument(),
    )
    // Unread rows carry a dot and the spoken "unread".
    expect(screen.getAllByTestId('unread-dot')).toHaveLength(1)
    expect(screen.getByText(/5 minutes ago, unread/)).toBeInTheDocument()
    // The unavailable actor shows a silhouette and the row has no link.
    expect(screen.getByTestId('avatar-silhouette')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /no longer available/ })).toBeNull()
  })

  it('opens a row’s link and marks it read', async () => {
    const api = mockApi({
      ...handlers(),
      'GET /deeds/deed-1': { status: 404, body: { detail: 'Not found', code: 'not_found' } },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/notifications')
    await user.click(await screen.findByRole('link', { name: /liked the deed about you/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/deeds/deed-1'))
    expect(api.calls('POST /notifications/n-1/read')).toHaveLength(1)
  })

  it('filters to unread rows and marks all as read', async () => {
    const api = mockApi(handlers())
    const user = userEvent.setup()
    renderApp('/notifications')
    await screen.findByText('You earned the First Witness badge.')
    await user.click(screen.getByRole('tab', { name: 'Unread' }))
    await waitFor(() =>
      expect(screen.queryByText('You earned the First Witness badge.')).toBeNull(),
    )
    expect(screen.getByText(/liked the deed about you/)).toBeInTheDocument()
    expect(api.calls('GET /notifications').some((r) => r.query.get('unread') === 'true')).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Mark all as read' }))
    await waitFor(() => expect(api.calls('POST /notifications/read-all')).toHaveLength(1))
    await waitFor(() => expect(screen.queryAllByTestId('unread-dot')).toHaveLength(0))
  })

  it('row menu: mark unread, mute the person, turn off the deed', async () => {
    const api = mockApi({
      ...handlers(),
      'PUT /notifications/mutes': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/notifications')
    await screen.findByText('You earned the First Witness badge.')
    const menus = screen.getAllByRole('button', { name: 'More options for this notification' })
    await user.click(menus[1])
    let sheet = screen.getByRole('dialog', { name: 'Notification options' })
    // A system row has no one to mute.
    expect(within(sheet).queryByRole('button', { name: /Mute/ })).toBeNull()
    await user.click(within(sheet).getByRole('button', { name: 'Mark as unread' }))
    await waitFor(() => expect(api.calls('POST /notifications/n-2/unread')).toHaveLength(1))
    await waitFor(() => expect(screen.getAllByTestId('unread-dot')).toHaveLength(2))

    await user.click(menus[0])
    sheet = screen.getByRole('dialog', { name: 'Notification options' })
    await user.click(within(sheet).getByRole('button', { name: 'Mute Ravi' }))
    await waitFor(() =>
      expect(api.calls('PUT /notifications/mutes')[0]?.body).toEqual({
        type: 'person',
        target: 'ravi',
      }),
    )
    expect(await screen.findByText(/Muted Ravi/)).toBeInTheDocument()

    await user.click(menus[0])
    sheet = screen.getByRole('dialog', { name: 'Notification options' })
    await user.click(
      within(sheet).getByRole('button', { name: 'Turn off notifications for this deed' }),
    )
    await waitFor(() =>
      expect(api.calls('PUT /notifications/mutes')[1]?.body).toEqual({
        type: 'deed',
        target: 'deed-1',
      }),
    )
  })

  it('says so when a newer unread row of the same group exists (409)', async () => {
    mockApi({
      ...handlers(),
      'POST /notifications/n-2/unread': {
        status: 409,
        body: { detail: 'Newer', code: 'newer_unread_exists' },
      },
    })
    const user = userEvent.setup()
    renderApp('/notifications')
    await screen.findByText('You earned the First Witness badge.')
    await user.click(
      screen.getAllByRole('button', { name: 'More options for this notification' })[1],
    )
    await user.click(screen.getByRole('button', { name: 'Mark as unread' }))
    expect(
      await screen.findByText('There’s already a newer unread notification like this one.'),
    ).toBeInTheDocument()
  })

  it('shows the empty and error states', async () => {
    mockApi(handlers([]))
    renderApp('/notifications')
    expect(await screen.findByText('Nothing yet')).toBeInTheDocument()
    expect(screen.getByText('When someone recognises you, you’ll see it here.')).toBeInTheDocument()
  })

  it('shows an error with Try again', async () => {
    mockApi({
      ...handlers(),
      'GET /notifications': {
        status: 500,
        body: { detail: 'Something went wrong. Please try again.' },
      },
    })
    renderApp('/notifications')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('notification settings', () => {
  it('switches categories; locked ones stay on', async () => {
    const api = mockApi({
      ...handlers(),
      'GET /notifications/preferences': { body: prefs() },
      'PUT /notifications/preferences': (req) => ({
        body: prefs({
          categories: prefs().categories.map((c) =>
            c.id === (req.body as { category: string }).category
              ? { ...c, on: (req.body as { on: boolean }).on }
              : c,
          ),
        }),
      }),
    })
    const user = userEvent.setup()
    renderApp('/settings/notifications')
    const activity = await screen.findByRole('switch', { name: 'Activity' })
    expect(activity).toHaveAttribute('aria-checked', 'false')
    await user.click(activity)
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Activity' })).toHaveAttribute(
        'aria-checked',
        'true',
      ),
    )
    expect(api.calls('PUT /notifications/preferences')[0].body).toEqual({
      category: 'activity',
      on: true,
    })
    const account = screen.getByRole('switch', { name: 'Account & safety' })
    expect(account).toBeDisabled()
    expect(account).toHaveAttribute('aria-checked', 'true')
    expect(
      screen.getByText('Always on, so you never miss something about your account.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Reviews' })).toBeDisabled()
    expect(
      screen.getByText('Always on, so you never miss something about a review.'),
    ).toBeInTheDocument()
  })

  it('lists muted people and deeds with Unmute', async () => {
    const api = mockApi({
      ...handlers(),
      'GET /notifications/preferences': { body: prefs() },
      'DELETE /notifications/mutes': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/settings/notifications')
    await user.click(await screen.findByRole('button', { name: 'Unmute Sneha Rao' }))
    await waitFor(() =>
      expect(api.calls('DELETE /notifications/mutes')[0]?.body).toEqual({
        type: 'person',
        target: 'sneha',
      }),
    )
    await user.click(
      screen.getByRole('button', { name: 'Unmute Ravi helped someone cross the road' }),
    )
    await waitFor(() =>
      expect(api.calls('DELETE /notifications/mutes')[1]?.body).toEqual({
        type: 'deed',
        target: 'deed-9',
      }),
    )
  })
})

describe('notification helpers', () => {
  it('maps links to routes', () => {
    expect(linkHref({ kind: 'deed', target: 'd1' })).toBe('/deeds/d1')
    expect(linkHref({ kind: 'my_deed', target: 'd1' })).toBe('/me/deeds/d1')
    expect(linkHref({ kind: 'reviews', target: null })).toBe('/me/reviews')
    expect(linkHref({ kind: 'home', target: null })).toBe('/')
    expect(linkHref({ kind: 'guidelines', target: 'community' })).toBe('/community-guidelines')
    expect(linkHref({ kind: 'guidelines', target: 'photo' })).toBe('/photo-guidelines')
    expect(linkHref({ kind: 'connections', target: 'followers' })).toBe(
      '/me/connections?tab=followers',
    )
    expect(linkHref(null)).toBeNull()
  })

  it('formats relative times', () => {
    const now = Date.parse('2026-11-27T12:00:00Z')
    expect(relativeTime('2026-11-27T11:59:40Z', now)).toBe('Just now')
    expect(relativeTime('2026-11-27T11:55:00Z', now)).toBe('5m')
    expect(relativeTime('2026-11-27T09:00:00Z', now)).toBe('3h')
    expect(relativeTime('2026-11-25T12:00:00Z', now)).toBe('2d')
    expect(relativeTime('2026-11-13T12:00:00Z', now)).toBe('2w')
    expect(relativeTime('2026-09-01T12:00:00Z', now)).toBe('1 Sept')
  })
})
