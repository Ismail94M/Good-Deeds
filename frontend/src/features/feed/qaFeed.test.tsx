/**
 * QA7 (M3 frontend): the PO rules from docs/M3-DESIGN.md §11 that the feature tests didn't pin
 * down yet: Top period persistence and wording (Q26), separate hides (Q24), removed photos (Q33),
 * doer-only photo ✕ (Q29), not-onboarded readers (Q42), self-likes (Q43), generic share text
 * (Q40), no coordinates on screen (§6.4), signed-URL refresh (§4.7), dialog focus and the
 * loading / error states of the new screens.
 */

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  makeMe,
  mockApi,
  NOT_ONBOARDED,
  renderApp,
  signedIn,
  type MockRequest,
} from '../../test/utils'
import type { Visibility } from './feedApi'
import {
  AHMED,
  FEED_OPTIONS,
  makeComment,
  makeDetail,
  makePhoto,
  makePost,
  makePublic,
  page,
  PRIYA,
} from './testFixtures'

const ME = makeMe({ handle: 'priya', display_name: 'Priya Sharma' })
const NEW_USER = makeMe({
  handle: 'rahulv',
  display_name: 'Rahul Verma',
  onboarding: NOT_ONBOARDED,
  phone_e164: null,
})

type Handlers = Parameters<typeof mockApi>[0]

function setup(handlers: Handlers = {}, me = ME) {
  return mockApi({
    ...signedIn(me),
    'GET /feed/options': { body: FEED_OPTIONS },
    'GET /feed': page([makePost()]),
    'GET /feed/me/notices': { body: [] },
    'GET /feed/me/comment-notices': { body: [] },
    'GET /feed/deeds/d1': { body: makeDetail() },
    'GET /feed/deeds/d1/comments': page([makeComment(1)]),
    ...handlers,
  })
}

const LISTED: Visibility = {
  listed: true,
  hidden_by_me: false,
  hidden_by_other: false,
  can_hide: true,
  can_show: false,
}

afterEach(() => {
  vi.useRealTimers()
})

// ----------------------------------------------------------------- Q26: Top period

describe('Top period (Q26)', () => {
  const topByPeriod = (req: MockRequest) => {
    if (req.query.get('tab') !== 'top') return page([makePost()])
    const period = req.query.get('period')
    return page([makePost({ deed_id: `t-${period}`, rank: 1 })])
  }

  it('the year period has its own intro, badge wording and list heading', async () => {
    setup({ 'GET /feed': topByPeriod })
    renderApp('/?tab=top&period=year')
    expect(await screen.findByText('#1 this year')).toBeInTheDocument()
    expect(
      screen.getByText('The most appreciated deeds published in the last 365 days.'),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'Top this year (last 365 days)' }),
    ).toBeInTheDocument()
  })

  it('takes the window lengths from /feed/options', async () => {
    setup({
      'GET /feed': topByPeriod,
      'GET /feed/options': {
        body: {
          ...FEED_OPTIONS,
          top_periods: [
            { id: 'week', days: 7 },
            { id: 'month', days: 28 },
            { id: 'year', days: 365 },
          ],
        },
      },
    })
    renderApp('/?tab=top&period=month')
    expect(
      await screen.findByRole('option', { name: 'Top this month (last 28 days)' }),
    ).toBeInTheDocument()
    expect(
      await screen.findByText('The most appreciated deeds published in the last 28 days.'),
    ).toBeInTheDocument()
  })

  it('going back from a deed keeps the chosen period', async () => {
    const api = setup({ 'GET /feed': topByPeriod })
    const user = userEvent.setup()
    const { router } = renderApp('/?tab=top')
    await screen.findByText('#1 this week')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Period' }), 'month')
    await screen.findByText('#1 this month')
    // Open the deed, then go back.
    await user.click(screen.getByRole('link', { name: 'Comments. 5 comments' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/deeds/t-month'))
    await act(() => router.navigate(-1))
    expect(await screen.findByRole('combobox', { name: 'Period' })).toHaveValue('month')
    expect(router.state.location.search).toBe('?tab=top&period=month')
    expect(await screen.findByText('#1 this month')).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(3)
    expect(api.calls('GET /feed').every((r) => r.query.get('period') !== 'decade')).toBe(true)
  })

  it('an unknown ?period= falls back to week', async () => {
    const api = setup({ 'GET /feed': topByPeriod })
    renderApp('/?tab=top&period=decade')
    expect(await screen.findByRole('combobox', { name: 'Period' })).toHaveValue('week')
    expect(await screen.findByText('#1 this week')).toBeInTheDocument()
    expect(api.calls('GET /feed').map((r) => r.query.get('period'))).not.toContain('decade')
  })

  it('shows no rank badge on Nearby or Following, even if a rank came back', async () => {
    setup({ 'GET /feed': page([makePost({ rank: 1 })]) })
    renderApp('/')
    await screen.findByRole('article')
    expect(screen.queryByText(/#1/)).not.toBeInTheDocument()
  })

  it('the tabs form a tablist whose tabs control the list panel', async () => {
    setup()
    renderApp('/')
    const tablist = await screen.findByRole('tablist', { name: 'Feed' })
    const tabs = within(tablist).getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Nearby', 'Following', 'Top'])
    const panel = screen.getByRole('tabpanel')
    for (const tab of tabs) expect(tab).toHaveAttribute('aria-controls', panel.id)
    // Roving tabindex: only the selected tab is in the tab order.
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1])
  })

  it('End and Home move between tabs', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderApp('/')
    const nearby = await screen.findByRole('tab', { name: 'Nearby' })
    nearby.focus()
    await user.keyboard('{End}')
    expect(screen.getByRole('tab', { name: 'Top' })).toHaveFocus()
    expect(router.state.location.search).toBe('?tab=top')
    await user.keyboard('{Home}')
    expect(screen.getByRole('tab', { name: 'Nearby' })).toHaveAttribute('aria-selected', 'true')
  })
})

// ----------------------------------------------------------------- Q24: separate hides

describe('Separate hides (Q24)', () => {
  it('when both hid it, my banner says the other person hid it too; showing leaves their hide', async () => {
    const both: Visibility = {
      listed: false,
      hidden_by_me: true,
      hidden_by_other: true,
      can_hide: false,
      can_show: true,
    }
    const otherOnly: Visibility = {
      listed: false,
      hidden_by_me: false,
      hidden_by_other: true,
      can_hide: true,
      can_show: false,
    }
    const deed = (visibility: Visibility) =>
      makeDetail({ my_role: 'reporter', reporter: PRIYA, visibility })
    const api = setup({
      'GET /feed/deeds/d1': [{ body: deed(both) }, { body: deed(otherOnly) }],
      'POST /feed/deeds/d1/show': { body: otherOnly },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    expect(
      await screen.findByText(
        /Ahmed has hidden it too, so it stays hidden until they show it again\./,
      ),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show in feed' }))
    await waitFor(() => expect(api.calls('POST /feed/deeds/d1/show')).toHaveLength(1))
    // Still hidden, now only by Ahmed: no Show button, and no "back in the feed" message.
    expect(await screen.findByText('Ahmed hid this deed from the feed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show in feed' })).not.toBeInTheDocument()
    expect(screen.queryByText('It’s back in the public feed.')).not.toBeInTheDocument()
    // Still no share link while it is hidden.
    expect(screen.queryByRole('button', { name: 'Share' })).not.toBeInTheDocument()
  })

  it('the doer sees the doer wording and the hide dialog says "your profile"', async () => {
    setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({ my_role: 'doer', doer: PRIYA, reporter: AHMED, visibility: LISTED }),
      },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    expect(
      await screen.findByText('This deed is about you. It’s in the public feed.'),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Hide from feed' }))
    expect(screen.getByRole('dialog', { name: 'Hide this deed from the feed?' })).toHaveTextContent(
      'It stays verified and still counts on your profile and for points.',
    )
  })

  it('the hide dialog focuses Cancel, closes on Esc and returns focus to Hide from feed', async () => {
    const api = setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({ my_role: 'reporter', reporter: PRIYA, visibility: LISTED }),
      },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    const hide = await screen.findByRole('button', { name: 'Hide from feed' })
    await user.click(hide)
    const dialog = screen.getByRole('dialog', { name: 'Hide this deed from the feed?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(hide).toHaveFocus()
    expect(api.calls('POST /feed/deeds/d1/hide')).toHaveLength(0)
  })

  it('shows the hide error inside the dialog and keeps it open', async () => {
    setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({ my_role: 'reporter', reporter: PRIYA, visibility: LISTED }),
      },
      'POST /feed/deeds/d1/hide': {
        status: 409,
        body: { detail: 'This deed was withdrawn.', code: 'deed_not_hideable' },
      },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Hide from feed' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Hide from feed' }))
    expect(await within(dialog).findByText('This deed was withdrawn.')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('a member who is neither doer nor reporter gets no visibility row', async () => {
    setup({ 'GET /feed/deeds/d1': { body: makeDetail({ visibility: LISTED }) } })
    renderApp('/deeds/d1')
    await screen.findByRole('heading', { level: 1, name: 'Ahmed Khan' })
    expect(screen.queryByRole('button', { name: 'Hide from feed' })).not.toBeInTheDocument()
  })
})

// ----------------------------------------------------------------- Q33 / Q29: removed photos

describe('Removed photos (Q33) and the doer’s ✕ (Q29)', () => {
  it('feed card: three photos with one removed render as "1 / 2" with two dots and no trace', async () => {
    setup({ 'GET /feed': page([makePost({ photos: [makePhoto(1), makePhoto(3)] })]) })
    renderApp('/')
    const card = await screen.findByRole('article')
    const carousel = within(card).getByRole('region', { name: 'Photos' })
    expect(within(carousel).getByText('1 / 2')).toBeInTheDocument()
    expect(within(carousel).getByTestId('carousel-dots').children).toHaveLength(2)
    expect(
      within(card)
        .getAllByRole('img', { hidden: true })
        .map((i) => i.getAttribute('alt')),
    ).toEqual(['Photo 1 of 2', 'Photo 2 of 2'])
    expect(card).not.toHaveTextContent(/removed|placeholder|3 photos|of 3/i)
  })

  it('one photo left: no counter and no dots, just the photo', async () => {
    setup({ 'GET /feed': page([makePost({ photos: [makePhoto(2)] })]) })
    renderApp('/')
    const card = await screen.findByRole('article')
    expect(within(card).getByRole('img', { name: 'Photo 1 of 1' })).toBeInTheDocument()
    expect(within(card).queryByTestId('carousel-dots')).not.toBeInTheDocument()
    expect(within(card).queryByText(/\d \/ \d/)).not.toBeInTheDocument()
  })

  it('deed page and public page: the same remaining photos, no placeholder', async () => {
    setup({
      'GET /feed/deeds/d1': { body: makeDetail({ photos: [makePhoto(1), makePhoto(3)] }) },
    })
    renderApp('/deeds/d1')
    const carousel = await screen.findByRole('region', { name: 'Photos' })
    expect(within(carousel).getByText('1 / 2')).toBeInTheDocument()
    expect(within(carousel).getByTestId('carousel-dots').children).toHaveLength(2)
    expect(screen.getByRole('main')).not.toHaveTextContent(/photo removed|removed after review/i)
  })

  it('public page with every photo removed is a text-only card', async () => {
    mockApi({ 'GET /public/deeds/d1': { body: makePublic({ photos: [] }) } })
    renderApp('/d/d1')
    const card = await screen.findByRole('article')
    expect(within(card).queryByRole('img')).not.toBeInTheDocument()
    expect(card).not.toHaveTextContent(/removed/i)
    // No photo, so nothing to report: the menu offers Copy link only.
    await userEvent.setup().click(within(card).getByRole('button', { name: /More options/ }))
    expect(screen.queryByRole('button', { name: 'Report photo' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument()
  })

  it('the doer removes the last photo showing; the counter steps back and the feed copy updates', async () => {
    const photos = [makePhoto(1), makePhoto(2), makePhoto(3)]
    setup({
      'GET /feed': page([makePost({ my_role: 'doer', doer: PRIYA, photos })]),
      'GET /feed/deeds/d1': {
        body: makeDetail({
          my_role: 'doer',
          doer: PRIYA,
          reporter: AHMED,
          photos,
          visibility: LISTED,
        }),
      },
      'DELETE /media/photos/p3': { status: 204 },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/deeds/d1')
    const carousel = await screen.findByRole('region', { name: 'Photos' })
    carousel.focus()
    await user.keyboard('{ArrowRight}{ArrowRight}')
    expect(within(carousel).getByText('3 / 3')).toBeInTheDocument()
    await user.click(within(carousel).getByRole('button', { name: 'Remove photo 3 of 3' }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove photo' }),
    )
    expect(await screen.findByText('2 / 2')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /of 3/ })).not.toBeInTheDocument()
    // Back on the feed, the cached card no longer has the photo either (server agrees).
    await act(() => router.navigate('/'))
    const card = await screen.findByRole('article')
    await waitFor(() =>
      expect(
        within(card)
          .getAllByRole('img')
          .map((i) => i.getAttribute('src')),
      ).not.toContain('https://storage.test/p3-display.webp'),
    )
  })

  it('a failed removal stays in the dialog with the error and keeps the photo', async () => {
    setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({
          my_role: 'doer',
          doer: PRIYA,
          reporter: AHMED,
          photos: [makePhoto(1), makePhoto(2)],
          visibility: LISTED,
        }),
      },
      'DELETE /media/photos/p1': {
        status: 500,
        body: { detail: 'Something went wrong. Please try again.', code: 'x' },
      },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Remove photo 1 of 2' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this photo?' })
    await user.click(within(dialog).getByRole('button', { name: 'Remove photo' }))
    expect(await within(dialog).findByText(/Something went wrong/)).toBeInTheDocument()
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
  })

  it('feed cards never offer a ✕, not even to the doer', async () => {
    setup({
      'GET /feed': page([
        makePost({ my_role: 'doer', doer: PRIYA, photos: [makePhoto(1), makePhoto(2)] }),
      ]),
    })
    renderApp('/')
    await screen.findByRole('region', { name: 'Photos' })
    expect(screen.queryByRole('button', { name: /Remove photo/ })).not.toBeInTheDocument()
  })

  it('the photo reported is the one showing in the carousel', async () => {
    const api = setup({
      'GET /feed': page([makePost({ photos: [makePhoto(1), makePhoto(2)] })]),
      'GET /media/options': { status: 404, body: { detail: 'x', code: 'x' } },
      'POST /media/photos/p2/reports': { status: 202, body: { status: 'received' } },
    })
    const user = userEvent.setup()
    renderApp('/')
    const carousel = await screen.findByRole('region', { name: 'Photos' })
    carousel.focus()
    await user.keyboard('{ArrowRight}')
    await user.click(screen.getByRole('button', { name: 'More options for this deed' }))
    await user.click(screen.getByRole('button', { name: 'Report photo' }))
    const sheet = screen.getByRole('dialog', { name: 'Report this photo' })
    await user.click(within(sheet).getByRole('radio', { name: 'Something else' }))
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    await waitFor(() => expect(api.calls('POST /media/photos/p2/reports')).toHaveLength(1))
    expect(api.calls('POST /media/photos/p1/reports')).toHaveLength(0)
  })
})

// ----------------------------------------------------------------- Q42: not onboarded

describe('Not onboarded (Q42)', () => {
  const prompt = () => screen.getByRole('dialog', { name: 'Finish setting up' })

  it('can read the feed, but save and follow ask to finish setting up', async () => {
    const api = setup({}, NEW_USER)
    const user = userEvent.setup()
    renderApp('/')
    await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })

    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(prompt()).toHaveTextContent('Finish setting up to like, comment and follow.')
    await user.click(within(prompt()).getByRole('button', { name: 'Not now' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Follow Ahmed Khan' }))
    expect(prompt()).toBeInTheDocument()
    await user.click(within(prompt()).getByRole('button', { name: 'Not now' }))

    expect(api.calls('PUT /feed/deeds/d1/save')).toHaveLength(0)
    expect(api.calls('PUT /users/ahmedk/follow')).toHaveLength(0)
    // The buttons didn't change state.
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Follow Ahmed Khan' })).toBeInTheDocument()
  })

  it('Finish setting up returns to the page they were on', async () => {
    setup({}, NEW_USER)
    const user = userEvent.setup()
    const { router } = renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Like. 24 likes' }))
    await user.click(within(prompt()).getByRole('button', { name: 'Finish setting up' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
    expect(new URLSearchParams(router.state.location.search).get('next')).toBe('/deeds/d1')
  })

  it('can read a deed and its comments, but commenting and reporting a comment prompt setup', async () => {
    const api = setup({}, NEW_USER)
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    expect(await screen.findByText('Comment 1')).toBeInTheDocument()

    await user.type(screen.getByRole('textbox', { name: 'Add a comment' }), 'Lovely')
    await user.click(screen.getByRole('button', { name: 'Post' }))
    expect(prompt()).toBeInTheDocument()
    await user.click(within(prompt()).getByRole('button', { name: 'Not now' }))
    expect(api.calls('POST /feed/deeds/d1/comments')).toHaveLength(0)
    // The draft is kept for after setup.
    expect(screen.getByRole('textbox', { name: 'Add a comment' })).toHaveValue('Lovely')

    await user.click(screen.getByRole('button', { name: 'Report comment by Rahul Verma' }))
    expect(prompt()).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Report this comment' })).not.toBeInTheDocument()
  })

  it('a 403 onboarding_required from the server also shows the prompt (and rolls back)', async () => {
    // The session thinks setup is done, but the server disagrees (e.g. phone just removed).
    setup({
      'PUT /feed/deeds/d1/like': {
        status: 403,
        body: { detail: 'Finish setting up first.', code: 'onboarding_required' },
      },
    })
    const user = userEvent.setup()
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Like. 24 likes' }))
    expect(await screen.findByRole('dialog', { name: 'Finish setting up' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Like. 24 likes' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(screen.queryByText('That didn’t work. Please try again.')).not.toBeInTheDocument()
  })

  it('can still report a photo (safety reports never wait)', async () => {
    const api = setup(
      {
        'GET /feed': page([makePost({ photos: [makePhoto(1)] })]),
        'POST /media/photos/p1/reports': { status: 202, body: { status: 'received' } },
      },
      NEW_USER,
    )
    const user = userEvent.setup()
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'More options for this deed' }))
    await user.click(screen.getByRole('button', { name: 'Report photo' }))
    const sheet = screen.getByRole('dialog', { name: 'Report this photo' })
    await user.click(
      within(sheet).getByRole('radio', { name: 'A child is shown without a parent’s consent' }),
    )
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Thanks for telling us' })).toBeVisible()
    expect(api.calls('POST /media/photos/p1/reports')[0].body).toEqual({
      reason: 'child_no_guardian_consent',
    })
    expect(screen.queryByRole('dialog', { name: 'Finish setting up' })).not.toBeInTheDocument()
    // Media options need setup; the sheet falls back to its built-in labels instead.
    expect(api.calls('GET /media/options')).toHaveLength(0)
  })

  it('can share by link, but no share is recorded', async () => {
    const api = setup({}, NEW_USER)
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Share' }))
    await user.click(screen.getByRole('button', { name: 'Copy link' }))
    expect(writeText).toHaveBeenCalledWith('http://localhost:5173/d/d1')
    expect(await screen.findByText('Link copied')).toBeInTheDocument()
    expect(api.calls('POST /feed/deeds/d1/shares')).toHaveLength(0)
    expect(screen.queryByRole('dialog', { name: 'Finish setting up' })).not.toBeInTheDocument()
  })
})

// ----------------------------------------------------------------- Q43: self-likes

describe('Self-likes (Q43)', () => {
  it('the doer can like their own deed and the shown count includes it', async () => {
    const api = setup({
      'GET /feed': page([makePost({ my_role: 'doer', doer: PRIYA, like_count: 3 })]),
      'PUT /feed/deeds/d1/like': { body: { liked: true, like_count: 4 } },
    })
    const user = userEvent.setup()
    renderApp('/')
    const like = await screen.findByRole('button', { name: 'Like. 3 likes' })
    expect(like).toBeEnabled()
    await user.click(like)
    expect(await screen.findByRole('button', { name: 'Like. 4 likes' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(api.calls('PUT /feed/deeds/d1/like')).toHaveLength(1)
  })

  it('the reporter can like the deed they reported', async () => {
    const api = setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({
          my_role: 'reporter',
          reporter: PRIYA,
          like_count: 1,
          visibility: LISTED,
        }),
      },
      'PUT /feed/deeds/d1/like': { body: { liked: true, like_count: 2 } },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Like. 1 like' }))
    expect(await screen.findByRole('button', { name: 'Like. 2 likes' })).toBeInTheDocument()
    expect(api.calls('PUT /feed/deeds/d1/like')).toHaveLength(1)
  })
})

// ----------------------------------------------------------------- Q40: generic share text

describe('Share text (Q40)', () => {
  it('names neither the doer nor the area nor the description in what leaves the app', async () => {
    const shareFn = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'share', { value: shareFn, configurable: true })
    try {
      setup({ 'POST /feed/deeds/d1/shares': { status: 204 } })
      const user = userEvent.setup()
      renderApp('/')
      await user.click(await screen.findByRole('button', { name: 'Share' }))
      const sheet = screen.getByRole('dialog', { name: 'Share this deed' })
      const href = decodeURIComponent(
        within(sheet).getByRole('link', { name: 'WhatsApp' }).getAttribute('href') ?? '',
      )
      await user.click(
        within(sheet).getByRole('button', { name: 'More options (Instagram and other apps)' }),
      )
      const shared = JSON.stringify(shareFn.mock.calls[0][0])
      for (const leaked of ['Ahmed', 'Khan', 'Koramangala', 'Silk Board', 'elderly', 'Rahul']) {
        expect(href).not.toContain(leaked)
        expect(shared).not.toContain(leaked)
      }
      expect(href).toBe(
        'https://wa.me/?text=A good deed was shared with you on Good Deeds. http://localhost:5173/d/d1',
      )
    } finally {
      // @ts-expect-error: remove the stub again
      delete navigator.share
    }
  })

  it('the WhatsApp link opens in a new tab without an opener and records the share', async () => {
    const api = setup({ 'POST /feed/deeds/d1/shares': { status: 204 } })
    const user = userEvent.setup()
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Share' }))
    const link = screen.getByRole('link', { name: 'WhatsApp' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    link.addEventListener('click', (e) => e.preventDefault())
    await user.click(link)
    await waitFor(() =>
      expect(api.calls('POST /feed/deeds/d1/shares')[0]?.body).toEqual({ target: 'whatsapp' }),
    )
  })

  it('a hidden deed has no Share and no Copy link on its own page', async () => {
    setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({
          my_role: 'reporter',
          reporter: PRIYA,
          visibility: {
            listed: false,
            hidden_by_me: true,
            hidden_by_other: false,
            can_hide: false,
            can_show: true,
          },
        }),
      },
    })
    renderApp('/deeds/d1')
    await screen.findByRole('button', { name: 'Show in feed' })
    expect(screen.queryByRole('button', { name: 'Share' })).not.toBeInTheDocument()
    // No photos and no share link: nothing for the ⋯ menu to offer.
    expect(
      screen.queryByRole('button', { name: 'More options for this deed' }),
    ).not.toBeInTheDocument()
  })
})

// ----------------------------------------------------------------- §6.4: no coordinates

describe('No coordinates on screen (§6.4)', () => {
  // Even if a response ever carried location fields, no screen may render them.
  const leaky = {
    lat: 12.93456,
    lng: 77.62468,
    location: { lat: 12.93456, lng: 77.62468 },
    accuracy_m: 17,
    phone_e164: '+919845012345',
  }
  const COORD = /12\.93|77\.62|\+91|9845012345/

  it('feed card', async () => {
    setup({ 'GET /feed': page([{ ...makePost(), ...leaky }]) })
    renderApp('/')
    const card = await screen.findByRole('article')
    expect(document.body.textContent).not.toMatch(COORD)
    expect(card.innerHTML).not.toMatch(COORD)
  })

  it('deed page', async () => {
    setup({ 'GET /feed/deeds/d1': { body: { ...makeDetail(), ...leaky } } })
    renderApp('/deeds/d1')
    await screen.findByText('Comment 1')
    expect(document.body.innerHTML).not.toMatch(COORD)
  })

  it('public page', async () => {
    mockApi({ 'GET /public/deeds/d1': { body: { ...makePublic(), ...leaky } } })
    renderApp('/d/d1')
    await screen.findByRole('article')
    expect(document.body.innerHTML).not.toMatch(COORD)
  })
})

// ----------------------------------------------------------------- §4.7: signed URL refresh

describe('Signed-URL refresh (§4.7)', () => {
  it('an image that fails to load triggers one refetch, not a loop', async () => {
    const api = setup({ 'GET /feed': page([makePost({ photos: [makePhoto(1), makePhoto(2)] })]) })
    renderApp('/')
    const img = await screen.findByRole('img', { name: 'Photo 1 of 2' })
    // Let the first load settle, then count from there.
    await new Promise((r) => setTimeout(r, 100))
    const before = api.calls('GET /feed').length
    fireEvent.error(img)
    await waitFor(() => expect(api.calls('GET /feed')).toHaveLength(before + 1))
    fireEvent.error(screen.getByRole('img', { name: 'Photo 1 of 2' }))
    fireEvent.error(document.querySelector('img[alt="Photo 2 of 2"]')!)
    await new Promise((r) => setTimeout(r, 100))
    expect(api.calls('GET /feed')).toHaveLength(before + 1)
  })

  it('the deed page refetches when its URLs are about to expire', async () => {
    const soon = new Date(Date.now() + 4 * 60_000).toISOString()
    const later = new Date(Date.now() + 60 * 60_000).toISOString()
    const api = setup({
      'GET /feed/deeds/d1': [
        { body: makeDetail({ photos: [makePhoto(1, { urls_expire_at: soon })] }) },
        { body: makeDetail({ photos: [makePhoto(1, { urls_expire_at: later })] }) },
      ],
    })
    renderApp('/deeds/d1')
    await screen.findByRole('img', { name: 'Photo 1 of 1' })
    await waitFor(() => expect(api.calls('GET /feed/deeds/d1')).toHaveLength(2))
    await new Promise((r) => setTimeout(r, 50))
    expect(api.calls('GET /feed/deeds/d1')).toHaveLength(2)
  })
})

// ----------------------------------------------------------------- Q38 / Q41: comments and notices

describe('Comments (Q35, Q37, Q38)', () => {
  it('the composer stops at the config limit, and the error clears when the text changes', async () => {
    setup({
      'GET /feed/options': { body: { ...FEED_OPTIONS, comment_max_chars: 280 } },
      'POST /feed/deeds/d1/comments': {
        status: 422,
        body: { detail: 'x', code: 'comment_has_link' },
      },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    const box = await screen.findByRole('textbox', { name: 'Add a comment' })
    await waitFor(() => expect(box).toHaveAttribute('maxLength', '280'))
    await user.type(box, 'see www.example.com')
    await user.click(screen.getByRole('button', { name: 'Post' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Links aren’t allowed in comments.')
    // The error is tied to the box for screen readers.
    const alert = screen.getByRole('alert')
    expect(box.getAttribute('aria-describedby')).toContain(alert.id)
    await user.type(box, '!')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(box).not.toHaveAttribute('aria-invalid')
  })

  it('the unkind-word message never repeats the word', async () => {
    setup({
      'POST /feed/deeds/d1/comments': {
        status: 422,
        body: { detail: 'Contains "badword"', code: 'comment_not_kind' },
      },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.type(await screen.findByRole('textbox', { name: 'Add a comment' }), 'badword')
    await user.click(screen.getByRole('button', { name: 'Post' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Please keep it kind.')
    expect(alert).not.toHaveTextContent('badword')
  })

  it('Post is disabled for an empty or whitespace-only comment', async () => {
    setup()
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    const post = await screen.findByRole('button', { name: 'Post' })
    expect(post).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Add a comment' }), '    ')
    expect(post).toBeDisabled()
  })

  it('the doer sees the doer note and both Delete and Report on other people’s comments', async () => {
    setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({ my_role: 'doer', doer: PRIYA, reporter: AHMED, visibility: LISTED }),
      },
      'GET /feed/deeds/d1/comments': page([makeComment(1, { can_delete: true })]),
    })
    renderApp('/deeds/d1')
    await screen.findByText('Comment 1')
    expect(
      screen.getByText('You see Delete on every comment because this deed is about you.'),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Delete comment by Rahul Verma' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Report comment by Rahul Verma' }),
    ).toBeInTheDocument()
  })

  it('the report-comment sheet closes on Esc and returns focus to its ⋯ button', async () => {
    setup()
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    const dots = await screen.findByRole('button', { name: 'Report comment by Rahul Verma' })
    await user.click(dots)
    const sheet = screen.getByRole('dialog', { name: 'Report this comment' })
    expect(sheet).toHaveAttribute('aria-modal', 'true')
    expect(sheet).toContainElement(document.activeElement as HTMLElement)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(dots).toHaveFocus()
  })

  it('shows a comments loading state, then an error with Try again', async () => {
    setup({
      'GET /feed/deeds/d1/comments': [
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        page([makeComment(1)]),
      ],
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    expect(await screen.findByText('Loading comments…')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Comment 1')).toBeInTheDocument()
  })

  it('shows the empty comments state', async () => {
    setup({ 'GET /feed/deeds/d1/comments': page([]) })
    renderApp('/deeds/d1')
    expect(
      await screen.findByText('No comments yet. Be the first to say something kind.'),
    ).toBeInTheDocument()
  })
})

describe('One-time notices (Q41, §3.4a)', () => {
  it('the public-feed card does not come back after dismissing it and returning to the feed', async () => {
    const api = setup({
      'GET /feed/me/notices': [{ body: ['public_feed_intro'] }, { body: [] }],
      'POST /feed/me/notices/public_feed_intro/seen': { status: 204 },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/')
    const card = await screen.findByRole('region', { name: 'About the public feed' })
    await user.click(within(card).getByRole('button', { name: 'OK' }))
    await waitFor(() =>
      expect(api.calls('POST /feed/me/notices/public_feed_intro/seen')).toHaveLength(1),
    )
    await act(() => router.navigate('/me/saved'))
    await act(() => router.navigate('/'))
    await screen.findByRole('article')
    expect(screen.queryByRole('region', { name: 'About the public feed' })).not.toBeInTheDocument()
  })

  it('a dismissed comment-removed card does not come back on the next visit', async () => {
    const notice = {
      comment_id: 'c1',
      deed_id: 'd1',
      doer: AHMED,
      excerpt: 'You are the worst',
      removed_at: '2026-11-17T10:00:00Z',
    }
    const api = setup({
      'GET /feed/me/comment-notices': [{ body: [notice] }, { body: [] }],
      'POST /feed/me/comment-notices/c1/seen': { status: 204 },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/')
    const card = await screen.findByRole('region', {
      name: 'Your comment on Ahmed Khan’s deed was removed',
    })
    await user.click(within(card).getByRole('button', { name: 'OK' }))
    await waitFor(() => expect(api.calls('POST /feed/me/comment-notices/c1/seen')).toHaveLength(1))
    await act(() => router.navigate('/me/saved'))
    await act(() => router.navigate('/'))
    await screen.findByRole('article')
    expect(screen.queryByRole('heading', { name: /deed was removed/ })).not.toBeInTheDocument()
  })

  it('the comment-removed card names no reporter or moderator', async () => {
    setup({
      'GET /feed/me/comment-notices': {
        body: [
          {
            comment_id: 'c1',
            deed_id: 'd1',
            doer: AHMED,
            excerpt: 'Rude words',
            removed_at: '2026-11-17T10:00:00Z',
          },
        ],
      },
    })
    renderApp('/')
    const card = await screen.findByRole('region', {
      name: 'Your comment on Ahmed Khan’s deed was removed',
    })
    expect(card).not.toHaveTextContent(/reported by|moderator|admin|Rahul/i)
  })
})

// ----------------------------------------------------------------- loading and error states

describe('Loading and error states of the new screens', () => {
  it('deed page: loading, then a server error with Try again (not the 404 page)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    setup({
      'GET /feed/deeds/d1': [
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        { body: makeDetail() },
      ],
    })
    renderApp('/deeds/d1')
    expect(await screen.findByText('Loading the deed…')).toBeInTheDocument()
    // Server errors are retried (network blips) before the error shows.
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.queryByText('This deed isn’t available')).not.toBeInTheDocument()
    vi.useRealTimers()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Ahmed Khan' })).toBeVisible()
  })

  it('a 404 on the deed page is not retried', async () => {
    const api = setup({
      'GET /feed/deeds/d1': { status: 404, body: { detail: 'x', code: 'deed_not_found' } },
    })
    renderApp('/deeds/d1')
    await screen.findByRole('heading', { name: 'This deed isn’t available' })
    expect(api.calls('GET /feed/deeds/d1')).toHaveLength(1)
  })

  it('public page: a server error shows a message, not the unavailable page', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockApi({
      'GET /public/deeds/d1': {
        status: 500,
        body: { detail: 'Something went wrong. Please try again.', code: 'x' },
      },
    })
    renderApp('/d/d1')
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.queryByText('This deed isn’t available')).not.toBeInTheDocument()
  })

  it('Saved: skeleton while loading', async () => {
    setup({ 'GET /me/saved': () => new Promise(() => {}) })
    renderApp('/me/saved')
    expect(
      await screen.findByRole('status', { name: 'Loading your saved deeds' }),
    ).toBeInTheDocument()
  })

  it('Saved: error with Try again', async () => {
    setup({
      'GET /me/saved': [
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        page([makePost()]),
      ],
    })
    const user = userEvent.setup()
    renderApp('/me/saved')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })).toBeInTheDocument()
  })

  it('Connections: error with Try again', async () => {
    setup({
      'GET /users/priya/social': {
        body: { followers: 1, following: 0, is_following: null, is_me: true },
      },
      'GET /me/followers': [
        { status: 500, body: { detail: 'Something went wrong. Please try again.', code: 'x' } },
        page([{ user: { handle: 'kavya', display_name: 'Kavya Nair' }, following: false }]),
      ],
    })
    const user = userEvent.setup()
    renderApp('/me/connections')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Kavya Nair')).toBeInTheDocument()
  })

  it('Connections: the tabs are a tablist with arrow-key support', async () => {
    setup({
      'GET /users/priya/social': {
        body: { followers: 1, following: 0, is_following: null, is_me: true },
      },
      'GET /me/followers': page([]),
      'GET /me/following': page([]),
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/connections')
    const followers = await screen.findByRole('tab', { name: /Followers/ })
    expect(followers.closest('[role="tablist"]')).not.toBeNull()
    followers.focus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: /Following/ })).toHaveAttribute('aria-selected', 'true')
    expect(router.state.location.search).toBe('?tab=following')
  })

  it('another person’s profile: their deeds list shows its empty state', async () => {
    setup({
      'GET /users/asha': {
        body: {
          handle: 'asha',
          display_name: 'Asha',
          bio: null,
          joined_at: '2026-11-01T10:00:00Z',
        },
      },
      'GET /users/asha/deed-counts': { body: { verified: 0 } },
      'GET /scoring/users/asha': {
        body: {
          tier: { slug: 'seed', label: 'Seed' },
          badges_total: 0,
          titles_total: 0,
          badges: [],
          titles: [],
        },
      },
      'GET /users/asha/social': {
        body: { followers: 0, following: 0, is_following: false, is_me: false },
      },
      'GET /feed/users/asha/deeds': page([]),
    })
    renderApp('/u/asha')
    expect(await screen.findByRole('heading', { name: 'Asha’s deeds' })).toBeInTheDocument()
    // Counts stay plain text (Q36), even at zero.
    const counts = screen.getByRole('group', { name: 'Counts' })
    expect(within(counts).queryByRole('link')).not.toBeInTheDocument()
    await waitFor(() => expect(within(counts).getAllByText('0')).toHaveLength(5))
  })
})
