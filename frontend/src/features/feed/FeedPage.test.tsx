import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import {
  makeMe,
  mockApi,
  NOT_ONBOARDED,
  renderApp,
  signedIn,
  type MockRequest,
} from '../../test/utils'
import { AHMED, FEED_OPTIONS, makePhoto, makePost, page } from './testFixtures'

const ME = makeMe({ handle: 'priya', display_name: 'Priya Sharma' })

function feedHandler(byTab: Record<string, ReturnType<typeof page>>) {
  return (req: MockRequest) => {
    const tab = req.query.get('tab') ?? 'nearby'
    const key = tab === 'top' ? `top-${req.query.get('period')}` : tab
    return byTab[key] ?? page([])
  }
}

function setup(handlers: Record<string, unknown> = {}, me = ME) {
  return mockApi({
    ...signedIn(me),
    'GET /feed/options': { body: FEED_OPTIONS },
    'GET /feed': feedHandler({ nearby: page([makePost()]) }),
    ...(handlers as Record<string, never>),
  })
}

describe('FeedPage: tabs and lists', () => {
  it('shows Nearby by default with its intro, a card and the end message', async () => {
    const api = setup()
    renderApp('/')
    const card = await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })
    expect(screen.getByRole('tab', { name: 'Nearby' })).toHaveAttribute('aria-selected', 'true')
    expect(
      screen.getByText('Good deeds verified around Bangalore, newest first.'),
    ).toBeInTheDocument()
    expect(within(card).getByText('recognised by Rahul Verma')).toBeInTheDocument()
    expect(within(card).getByText('Koramangala')).toBeInTheDocument()
    expect(within(card).getByText('Verified by a witness')).toBeInTheDocument()
    expect(within(card).getByText('2 h ago')).toBeInTheDocument()
    expect(screen.getByText('You’re all caught up')).toBeInTheDocument()
    expect(api.calls('GET /feed')[0].query.get('tab')).toBe('nearby')
    expect(api.calls('GET /feed')[0].query.has('period')).toBe(false)
  })

  it('shows two skeleton cards while loading', async () => {
    setup({ 'GET /feed': () => new Promise(() => {}) })
    renderApp('/')
    expect(await screen.findByRole('status', { name: 'Loading the feed' })).toBeInTheDocument()
  })

  it('shows the Following empty state and Browse Nearby goes back to Nearby', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderApp('/?tab=following')
    expect(await screen.findByText('You’re not following anyone yet')).toBeInTheDocument()
    expect(screen.getByText('Deeds by people you follow.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Browse Nearby' }))
    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })).toBeInTheDocument()
  })

  it('switches tabs with the keyboard and keeps the tab in the URL', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderApp('/')
    const nearby = await screen.findByRole('tab', { name: 'Nearby' })
    nearby.focus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Following' })).toHaveAttribute('aria-selected', 'true')
    expect(router.state.location.search).toBe('?tab=following')
  })

  it('shows the Nearby empty state', async () => {
    setup({ 'GET /feed': page([]) })
    renderApp('/')
    expect(await screen.findByText('No deeds yet')).toBeInTheDocument()
    const main = screen.getByRole('main')
    expect(within(main).getByRole('link', { name: 'Record a deed' })).toHaveAttribute(
      'href',
      '/report',
    )
  })

  it('shows an error with Try again', async () => {
    setup({
      'GET /feed': [
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        page([makePost()]),
      ],
    })
    const user = userEvent.setup()
    renderApp('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })).toBeInTheDocument()
  })

  it('loads the next page with Show more', async () => {
    const api = setup({
      'GET /feed': (req: MockRequest) =>
        req.query.get('cursor') === 'c2'
          ? page([
              makePost({ deed_id: 'd2', doer: { handle: 'kavya', display_name: 'Kavya Nair' } }),
            ])
          : page([makePost()], 'c2'),
    })
    const user = userEvent.setup()
    renderApp('/')
    await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })
    expect(screen.queryByText('You’re all caught up')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show more' }))
    expect(await screen.findByRole('article', { name: 'Deed by Kavya Nair' })).toBeInTheDocument()
    expect(screen.getByText('You’re all caught up')).toBeInTheDocument()
    expect(api.calls('GET /feed')[1].query.get('cursor')).toBe('c2')
  })
})

describe('FeedPage: Top and its period', () => {
  const top = () =>
    feedHandler({
      'top-week': page([
        makePost({ deed_id: 'a', rank: 1 }),
        makePost({ deed_id: 'b', rank: 3, doer: { handle: 'sneha', display_name: 'Sneha Rao' } }),
        makePost({ deed_id: 'c', rank: 4, doer: { handle: 'imran', display_name: 'Imran S' } }),
      ]),
      'top-month': page([makePost({ deed_id: 'm', rank: 2 })]),
    })

  it('shows the dropdown only on Top, with the three rolling labels and week by default', async () => {
    setup({ 'GET /feed': top() })
    const user = userEvent.setup()
    renderApp('/')
    await screen.findByText('No deeds yet')
    expect(screen.queryByRole('combobox', { name: 'Period' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Top' }))
    const select = await screen.findByRole('combobox', { name: 'Period' })
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual([
      'Top this week (last 7 days)',
      'Top this month (last 30 days)',
      'Top this year (last 365 days)',
    ])
    expect(select).toHaveValue('week')
    expect(screen.getAllByRole('tab')).toHaveLength(3)
    expect(
      screen.getByText('The most appreciated deeds published in the last 7 days.'),
    ).toBeInTheDocument()
  })

  it('badges only ranks up to top_badge_ranks, with the period wording', async () => {
    setup({ 'GET /feed': top() })
    renderApp('/?tab=top')
    expect(await screen.findByText('#1 this week')).toBeInTheDocument()
    expect(screen.getByText('#3 this week')).toBeInTheDocument()
    expect(screen.queryByText(/#4/)).not.toBeInTheDocument()
  })

  it('changing the period replaces ?period=, refetches from rank 1 and moves focus', async () => {
    const api = setup({ 'GET /feed': top() })
    const user = userEvent.setup()
    const { router } = renderApp('/?tab=top')
    await screen.findByText('#1 this week')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Period' }), 'month')
    expect(await screen.findByText('#2 this month')).toBeInTheDocument()
    expect(router.state.location.search).toBe('?tab=top&period=month')
    expect(router.state.historyAction).toBe('REPLACE')
    const last = api.calls('GET /feed').at(-1)!
    expect(last.query.get('period')).toBe('month')
    expect(last.query.has('cursor')).toBe(false)
    expect(screen.getByRole('heading', { name: 'Top this month (last 30 days)' })).toHaveFocus()
    expect(
      screen.getByText('The most appreciated deeds published in the last 30 days.'),
    ).toBeInTheDocument()
  })

  it('keeps the period when going Top → Nearby → Top', async () => {
    setup({ 'GET /feed': top() })
    const user = userEvent.setup()
    const { router } = renderApp('/?tab=top')
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Period' }), 'month')
    await user.click(screen.getByRole('tab', { name: 'Nearby' }))
    expect(router.state.location.search).toBe('')
    await user.click(screen.getByRole('tab', { name: 'Top' }))
    expect(router.state.location.search).toBe('?tab=top&period=month')
    expect(screen.getByRole('combobox', { name: 'Period' })).toHaveValue('month')
  })

  it('opening /?tab=top&period=year keeps the year', async () => {
    const api = setup({ 'GET /feed': top() })
    renderApp('/?tab=top&period=year')
    expect(await screen.findByRole('combobox', { name: 'Period' })).toHaveValue('year')
    await waitFor(() => expect(api.calls('GET /feed')[0].query.get('period')).toBe('year'))
  })
})

describe('PostCard', () => {
  it('shows photos with a counter and dots; arrow keys move between them', async () => {
    setup({
      'GET /feed': page([makePost({ photos: [makePhoto(1), makePhoto(2)] })]),
    })
    const user = userEvent.setup()
    renderApp('/')
    const carousel = await screen.findByRole('region', { name: 'Photos' })
    expect(within(carousel).getByText('1 / 2')).toBeInTheDocument()
    expect(within(carousel).getByTestId('carousel-dots').children).toHaveLength(2)
    expect(within(carousel).getByRole('img', { name: 'Photo 1 of 2' })).toBeInTheDocument()
    carousel.focus()
    await user.keyboard('{ArrowRight}')
    expect(within(carousel).getByText('2 / 2')).toBeInTheDocument()
  })

  it('renders a text-only card when no photos are left (Q33), with no placeholder', async () => {
    setup({ 'GET /feed': page([makePost({ photos: [] })]) })
    renderApp('/')
    const card = await screen.findByRole('article')
    expect(within(card).queryByRole('img')).not.toBeInTheDocument()
    expect(within(card).queryByText(/removed/i)).not.toBeInTheDocument()
    expect(within(card).queryByText(/\d \/ \d/)).not.toBeInTheDocument()
  })

  it('hides Follow on my own deed and says "recognised by you" on my reports', async () => {
    setup({
      'GET /feed': page([
        makePost({
          deed_id: 'mine',
          my_role: 'doer',
          doer: { handle: 'priya', display_name: 'Priya Sharma' },
        }),
        makePost({ deed_id: 'reported', my_role: 'reporter' }),
      ]),
    })
    renderApp('/')
    const mine = await screen.findByRole('article', { name: 'Deed by Priya Sharma' })
    expect(within(mine).queryByRole('button', { name: /follow/i })).not.toBeInTheDocument()
    const reported = screen.getByRole('article', { name: 'Deed by Ahmed Khan' })
    expect(within(reported).getByText('recognised by you')).toBeInTheDocument()
    expect(within(reported).getByRole('button', { name: 'Follow Ahmed Khan' })).toBeInTheDocument()
  })

  it('likes optimistically and keeps the server count', async () => {
    let release: () => void = () => {}
    const api = setup({
      'PUT /feed/deeds/d1/like': () =>
        new Promise((resolve) => {
          release = () => resolve({ body: { liked: true, like_count: 30 } })
        }),
    })
    const user = userEvent.setup()
    renderApp('/')
    const like = await screen.findByRole('button', { name: 'Like. 24 likes' })
    expect(like).toHaveAttribute('aria-pressed', 'false')
    await user.click(like)
    expect(screen.getByRole('button', { name: 'Like. 25 likes' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    act(() => release())
    expect(await screen.findByRole('button', { name: 'Like. 30 likes' })).toBeInTheDocument()
    expect(api.calls('PUT /feed/deeds/d1/like')).toHaveLength(1)
  })

  it('rolls a like back on error and says so', async () => {
    setup({
      'PUT /feed/deeds/d1/like': {
        status: 429,
        body: { detail: 'Slow down', code: 'rate_limited' },
      },
    })
    const user = userEvent.setup()
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Like. 24 likes' }))
    expect(await screen.findByText('That didn’t work. Please try again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Like. 24 likes' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('saves and unsaves with the right labels, rolling back on error', async () => {
    setup({
      'PUT /feed/deeds/d1/save': { body: { saved: true } },
      'DELETE /feed/deeds/d1/save': { status: 500, body: { detail: 'x', code: 'x' } },
    })
    const user = userEvent.setup()
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    const saved = await screen.findByRole('button', { name: 'Saved. Tap to unsave' })
    expect(saved).toHaveAttribute('aria-pressed', 'true')
    await user.click(saved)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Saved. Tap to unsave' })).toHaveAttribute(
        'aria-pressed',
        'true',
      ),
    )
  })

  it('follows the doer on every card at once and rolls back on error', async () => {
    const api = setup({
      'GET /feed': page([makePost(), makePost({ deed_id: 'd2' })]),
      'PUT /users/ahmedk/follow': [
        { body: { followers: 10, following: 2, is_following: true, is_me: false } },
      ],
      'DELETE /users/ahmedk/follow': { status: 500, body: { detail: 'x', code: 'x' } },
    })
    const user = userEvent.setup()
    renderApp('/')
    const buttons = await screen.findAllByRole('button', { name: 'Follow Ahmed Khan' })
    await user.click(buttons[0])
    expect(screen.getAllByRole('button', { name: 'Following Ahmed Khan' })).toHaveLength(2)
    expect(api.calls('PUT /users/ahmedk/follow')).toHaveLength(1)
    await user.click(screen.getAllByRole('button', { name: 'Following Ahmed Khan' })[1])
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Following Ahmed Khan' })).toHaveLength(2),
    )
  })

  it('asks a user who has not finished setting up to finish first (Q42)', async () => {
    const api = setup({}, makeMe({ onboarding: NOT_ONBOARDED }))
    const user = userEvent.setup()
    const { router } = renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Like. 24 likes' }))
    const dialog = screen.getByRole('dialog', { name: 'Finish setting up' })
    expect(dialog).toHaveTextContent('Finish setting up to like, comment and follow.')
    expect(api.calls('PUT /feed/deeds/d1/like')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Finish setting up' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
  })

  it('shows the API category label, and a readable slug only when it is missing', async () => {
    setup({
      'GET /feed': page([
        makePost(),
        makePost({ deed_id: 'd2', category: 'road_safety', category_label: '' }),
      ]),
    })
    renderApp('/')
    const [first, second] = await screen.findAllByRole('article')
    expect(within(first).getByText('Helping an elderly person')).toBeInTheDocument()
    expect(within(second).getByText('Road safety')).toBeInTheDocument()
  })

  it('links the comment count and the avatar', async () => {
    setup()
    renderApp('/')
    expect(await screen.findByRole('link', { name: 'Comments. 5 comments' })).toHaveAttribute(
      'href',
      '/deeds/d1',
    )
    expect(screen.getByRole('link', { name: 'Ahmed Khan, open profile' })).toHaveAttribute(
      'href',
      `/u/${AHMED.handle}`,
    )
  })
})

describe('Share sheet', () => {
  it('offers WhatsApp with the generic text, records each share and copies the link', async () => {
    const api = setup({ 'POST /feed/deeds/d1/shares': { status: 204 } })
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Share' }))
    const sheet = screen.getByRole('dialog', { name: 'Share this deed' })
    expect(sheet).toHaveTextContent('Ahmed Khan · Helping an elderly person')
    expect(within(sheet).queryByRole('button', { name: /More options/ })).not.toBeInTheDocument()
    const whatsapp = within(sheet).getByRole('link', { name: 'WhatsApp' })
    expect(whatsapp).toHaveAttribute(
      'href',
      `https://wa.me/?text=${encodeURIComponent(
        'A good deed was shared with you on Good Deeds. http://localhost:5173/d/d1',
      )}`,
    )
    expect(sheet).toHaveTextContent('If the deed is hidden, the link stops working.')
    await user.click(within(sheet).getByRole('button', { name: 'Copy link' }))
    expect(writeText).toHaveBeenCalledWith('http://localhost:5173/d/d1')
    expect(await screen.findByText('Link copied')).toBeInTheDocument()
    await waitFor(() => expect(api.calls('POST /feed/deeds/d1/shares')).toHaveLength(1))
    expect(api.calls('POST /feed/deeds/d1/shares')[0].body).toEqual({ target: 'copy_link' })
  })

  it('shows More options only with the Web Share API, and records it', async () => {
    const shareFn = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'share', { value: shareFn, configurable: true })
    try {
      const api = setup({ 'POST /feed/deeds/d1/shares': { status: 204 } })
      const user = userEvent.setup()
      renderApp('/')
      await user.click(await screen.findByRole('button', { name: 'Share' }))
      await user.click(
        screen.getByRole('button', { name: 'More options (Instagram and other apps)' }),
      )
      expect(shareFn).toHaveBeenCalledWith({
        title: 'A good deed was shared with you',
        text: 'A good deed was shared with you on Good Deeds.',
        url: 'http://localhost:5173/d/d1',
      })
      await waitFor(() =>
        expect(api.calls('POST /feed/deeds/d1/shares')[0]?.body).toEqual({ target: 'web_share' }),
      )
    } finally {
      // @ts-expect-error: remove the stub again
      delete navigator.share
    }
  })

  it('closes on Escape and returns focus to Share', async () => {
    setup()
    const user = userEvent.setup()
    renderApp('/')
    const share = await screen.findByRole('button', { name: 'Share' })
    await user.click(share)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(share).toHaveFocus()
  })

  it('the ⋯ menu offers Copy link (Report photo waits for F16)', async () => {
    setup({ 'GET /feed': page([makePost({ photos: [makePhoto(1)] })]) })
    const user = userEvent.setup()
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'More options for this deed' }))
    const menu = screen.getByRole('dialog', { name: 'Ahmed Khan’s deed' })
    expect(within(menu).getByRole('button', { name: 'Copy link' })).toBeInTheDocument()
  })
})
