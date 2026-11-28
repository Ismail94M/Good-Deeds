/** Good Deeds Stories (D73): the bar at the top of the feed and the full-screen viewer. */

import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn, type MockRequest } from '../../test/utils'
import type { StoryOut, StoryPerson, StoryRing, StoryRings } from './feedApi'
import { AHMED, FEED_OPTIONS, makePhoto, makePost, page, PRIYA, RAHUL } from './testFixtures'

const ME = makeMe({ handle: PRIYA.handle, display_name: PRIYA.display_name })
const SHIVA = { handle: 'shiva', display_name: 'Shiva Rao' }

function ring(overrides: Partial<StoryRing>): StoryRing {
  return {
    user: RAHUL,
    is_me: false,
    reported_count: 1,
    verified_count: 0,
    unseen_count: 1,
    latest_at: new Date().toISOString(),
    ...overrides,
  }
}

function rings(items: StoryRing[], overrides: Partial<StoryRings> = {}): StoryRings {
  return { items, seconds_per_story: 5, window_hours: 24, ...overrides }
}

function story(overrides: Partial<StoryOut> & { id: string }): StoryOut {
  const { id, ...rest } = overrides
  return { role: 'reported', seen: false, post: makePost({ deed_id: id }), ...rest }
}

function person(user: StoryPerson['user'], items: StoryOut[]): StoryPerson {
  return { user, is_me: false, seconds_per_story: 5, items }
}

// Rahul: a seen story then an unseen one (both reported). Shiva: one green (own deed verified).
const RAHUL_STORIES = person(RAHUL, [
  story({ id: 'r1', seen: true, post: makePost({ deed_id: 'r1', description: 'First deed' }) }),
  story({
    id: 'r2',
    post: makePost({
      deed_id: 'r2',
      description: 'Carried groceries for a neighbour',
      photos: [makePhoto(1), makePhoto(2)],
    }),
  }),
])
const SHIVA_STORIES = person(SHIVA, [
  story({
    id: 's1',
    role: 'verified',
    post: makePost({
      deed_id: 's1',
      doer: SHIVA,
      reporter: AHMED,
      description: 'Fed stray dogs near the park',
    }),
  }),
])

function setup(handlers: Record<string, unknown> = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /feed/options': { body: FEED_OPTIONS },
    'GET /feed': page([makePost()]),
    'GET /feed/stories': {
      body: rings([
        ring({ user: RAHUL, reported_count: 2, unseen_count: 1 }),
        ring({ user: SHIVA, reported_count: 0, verified_count: 1 }),
        ring({ user: AHMED, reported_count: 1, verified_count: 1, unseen_count: 0 }),
      ]),
    },
    'GET /feed/stories/rahulv': { body: RAHUL_STORIES },
    'GET /feed/stories/shiva': { body: SHIVA_STORIES },
    'PUT /feed/stories/seen': { status: 204 },
    ...(handlers as Record<string, never>),
  })
}

async function bar() {
  // The first query after renderApp gets extra time: it can be slow under full-suite load.
  return screen.findByRole('region', { name: 'Stories' }, { timeout: 5000 })
}

async function openRing(name: RegExp) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name }))
  const dialog = await screen.findByRole('dialog')
  return { user, dialog }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('StoriesBar', () => {
  it('shows my network’s rings above the feed, coloured by kind, with counts only', async () => {
    const api = setup()
    renderApp('/')
    const stories = await bar()
    const buttons = await within(stories).findAllByTestId('story-ring')
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Rahul Verma’s stories: 2 reported, 1 new',
      'Shiva Rao’s stories: 1 verified, 1 new',
      'Ahmed Khan’s stories: 1 verified, 1 reported, all seen',
    ])
    expect(buttons.map((b) => b.dataset.seen)).toEqual(['false', 'false', 'true'])
    // The ring colours: blue (reported), green (verified), grey (all seen).
    const colour = (b: HTMLElement) => (b.firstElementChild as HTMLElement).style.background
    expect(colour(buttons[0])).toBe('rgb(59, 130, 246)')
    expect(colour(buttons[1])).toBe('rgb(16, 185, 129)')
    expect(colour(buttons[2])).toBe('rgb(203, 213, 225)')
    const kind = (b: HTMLElement) => (b.firstElementChild as HTMLElement).dataset.ring
    expect(buttons.map(kind)).toEqual(['reported', 'verified', 'seen'])
    expect(stories).toHaveTextContent('Reported')
    expect(stories).toHaveTextContent('Verified')
    // No own ring: a shortcut to report a deed instead.
    expect(within(stories).getByRole('link', { name: 'Report a deed' })).toHaveAttribute(
      'href',
      '/report',
    )
    // It sits above the feed tabs.
    const tabs = screen.getByRole('tablist', { name: 'Feed' })
    expect(stories.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Lazy: nobody's stories (or photos) are loaded until a ring is opened.
    expect(api.requests.filter((r) => r.path.startsWith('/feed/stories/'))).toEqual([])
  })

  it('uses half blue, half green for someone with both kinds, and "Your story" for me', async () => {
    setup({
      'GET /feed/stories': {
        body: rings([ring({ user: PRIYA, is_me: true, reported_count: 1, verified_count: 2 })]),
      },
    })
    renderApp('/')
    const [mine] = await within(await bar()).findAllByTestId('story-ring')
    expect(mine).toHaveTextContent('Your story')
    expect(mine).toHaveAccessibleName('Your stories: 2 verified, 1 reported, 1 new')
    expect((mine.firstElementChild as HTMLElement).dataset.ring).toBe('both')
    expect(screen.queryByRole('link', { name: 'Report a deed' })).not.toBeInTheDocument()
  })

  it('explains the bar when nobody has stories', async () => {
    setup({ 'GET /feed/stories': { body: rings([]) } })
    renderApp('/')
    expect(await bar()).toHaveTextContent(
      'When people you follow, or who follow you, do or witness a good deed, it shows here for 24 hours.',
    )
  })

  it('leaves the bar out when stories can’t load, and the feed still works', async () => {
    setup({ 'GET /feed/stories': { status: 404, body: { detail: 'x', code: 'not_found' } } })
    renderApp('/')
    expect(await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Stories' })).not.toBeInTheDocument()
  })
})

describe('StoryViewer', () => {
  it('opens full screen at the first unwatched story with photo, line, details and progress', async () => {
    const api = setup()
    renderApp('/')
    const { dialog } = await openRing(/Rahul Verma’s stories/)
    expect(dialog).toHaveAccessibleName('Rahul Verma’s stories')
    expect(await within(dialog).findByText('Story 2 of 2')).toBeInTheDocument()
    expect(within(dialog).getAllByTestId('story-progress')).toHaveLength(2)
    const caption = within(dialog).getByTestId('story-caption')
    expect(caption).toHaveTextContent('Rahul Verma reported a kindness act by Ahmed Khan')
    expect(caption).toHaveTextContent('Helping an elderly person · Koramangala')
    expect(caption).toHaveTextContent('Carried groceries for a neighbour')
    expect(caption).toHaveTextContent('+1 more photo')
    expect(within(dialog).getByText('Reported')).toBeInTheDocument()
    expect(within(dialog).getByText('1 h ago')).toBeInTheDocument()
    expect(within(dialog).getByRole('img')).toHaveAttribute(
      'src',
      'https://storage.test/p1-display.webp',
    )
    expect(within(caption).getByRole('link', { name: 'View deed' })).toHaveAttribute(
      'href',
      '/deeds/r2',
    )
    expect(within(dialog).getByRole('button', { name: 'Close stories' })).toHaveFocus()
    // Marked watched once (the already-watched first story isn't sent again).
    await waitFor(() => expect(api.calls('PUT /feed/stories/seen')).toHaveLength(1))
    expect(api.calls('PUT /feed/stories/seen')[0].body).toEqual({
      deed_id: 'r2',
      role: 'reported',
    })
  })

  it('taps through stories and people, both ways, and closes after the last', async () => {
    const api = setup()
    renderApp('/')
    const { user, dialog } = await openRing(/Rahul Verma’s stories/)
    await within(dialog).findByText('Story 2 of 2')
    await user.click(within(dialog).getByRole('button', { name: 'Previous story' }))
    expect(within(dialog).getByText('Story 1 of 2')).toBeInTheDocument()
    // No photo: the deed's words fill the screen.
    expect(dialog).toHaveTextContent('First deed')

    await user.click(within(dialog).getByRole('button', { name: 'Next story' }))
    await user.click(within(dialog).getByRole('button', { name: 'Next story' }))
    // Next person: Shiva's green story.
    expect(await screen.findByRole('dialog', { name: 'Shiva Rao’s stories' })).toBeVisible()
    const caption = await screen.findByTestId('story-caption')
    expect(caption).toHaveTextContent('Shiva Rao’s kindness act was verified by Ahmed Khan')
    expect(within(screen.getByRole('dialog')).getByText('Verified')).toBeInTheDocument()

    // Back to Rahul's last story.
    await user.click(screen.getByRole('button', { name: 'Previous story' }))
    expect(await screen.findByRole('dialog', { name: 'Rahul Verma’s stories' })).toBeVisible()
    expect(screen.getByText('Story 2 of 2')).toBeInTheDocument()

    // Through Shiva, then Ahmed's ring has nothing mocked: it fails to load; skip past it closes.
    await user.click(screen.getByRole('button', { name: 'Next story' }))
    await user.click(await screen.findByRole('button', { name: 'Next story' }))
    expect(await screen.findByText('We couldn’t load these stories.')).toBeInTheDocument()
    await user.keyboard('{ArrowRight}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    // The bar refreshes so watched rings turn grey.
    await waitFor(() => expect(api.calls('GET /feed/stories').length).toBeGreaterThan(1))
  })

  it('moves on by itself after the story’s time, and pause stops the clock', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setup()
    renderApp('/')
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(await screen.findByRole('button', { name: /Shiva Rao’s stories/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Shiva Rao’s stories' })
    await within(dialog).findByText('Story 1 of 1')
    await user.click(within(dialog).getByRole('button', { name: 'Pause' }))
    expect(within(dialog).getByRole('button', { name: 'Play' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await act(async () => {
      vi.advanceTimersByTime(8000)
    })
    expect(screen.getByRole('dialog', { name: 'Shiva Rao’s stories' })).toBeVisible()

    await user.click(within(dialog).getByRole('button', { name: 'Play' }))
    await act(async () => {
      vi.advanceTimersByTime(5200)
    })
    // Shiva was the last unwatched-from-here person before Ahmed (not mocked).
    expect(await screen.findByRole('dialog', { name: 'Ahmed Khan’s stories' })).toBeVisible()
  })

  it('uses the keyboard: arrows move, Escape closes and gives focus back', async () => {
    setup()
    renderApp('/')
    const { user, dialog } = await openRing(/Rahul Verma’s stories/)
    await within(dialog).findByText('Story 2 of 2')
    await user.keyboard('{ArrowLeft}')
    expect(within(dialog).getByText('Story 1 of 2')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Rahul Verma’s stories/ })).toHaveFocus()
  })

  it('says "You" in my own stories', async () => {
    const mine = person(PRIYA, [
      story({ id: 'm1', post: makePost({ deed_id: 'm1', reporter: PRIYA, my_role: 'reporter' }) }),
      story({
        id: 'm2',
        role: 'verified',
        post: makePost({ deed_id: 'm2', doer: PRIYA, reporter: SHIVA, my_role: 'doer' }),
      }),
    ])
    setup({
      'GET /feed/stories': { body: rings([ring({ user: PRIYA, is_me: true })]) },
      'GET /feed/stories/priya': { body: { ...mine, is_me: true } },
    })
    renderApp('/')
    const { user, dialog } = await openRing(/Your stories/)
    const caption = await within(dialog).findByTestId('story-caption')
    expect(caption).toHaveTextContent('You reported a kindness act by Ahmed Khan')
    await user.click(within(dialog).getByRole('button', { name: 'Next story' }))
    expect(within(dialog).getByTestId('story-caption')).toHaveTextContent(
      'Your kindness act was verified by Shiva Rao',
    )
  })

  it('skips a person whose stories have all ended since the bar loaded', async () => {
    setup({ 'GET /feed/stories/rahulv': { body: person(RAHUL, []) } })
    renderApp('/')
    const { user, dialog } = await openRing(/Rahul Verma’s stories/)
    expect(await within(dialog).findByText('These stories have ended.')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(await screen.findByRole('dialog', { name: 'Shiva Rao’s stories' })).toBeVisible()
  })

  it('records the seen call body for a green story', async () => {
    const api = setup()
    renderApp('/')
    await openRing(/Shiva Rao’s stories/)
    await waitFor(() => expect(api.calls('PUT /feed/stories/seen')).toHaveLength(1))
    const req: MockRequest = api.calls('PUT /feed/stories/seen')[0]
    expect(req.body).toEqual({ deed_id: 's1', role: 'verified' })
  })
})
