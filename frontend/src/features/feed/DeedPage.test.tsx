import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { Visibility } from './feedApi'
import {
  AHMED,
  FEED_OPTIONS,
  makeComment,
  makeDetail,
  makePhoto,
  page,
  PRIYA,
} from './testFixtures'

const ME = makeMe({ handle: 'priya', display_name: 'Priya Sharma' })

const LISTED: Visibility = {
  listed: true,
  hidden_by_me: false,
  hidden_by_other: false,
  can_hide: true,
  can_show: false,
}
const HIDDEN_BY_ME: Visibility = {
  listed: false,
  hidden_by_me: true,
  hidden_by_other: false,
  can_hide: false,
  can_show: true,
}

function setup(handlers: Record<string, unknown> = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /feed/options': { body: FEED_OPTIONS },
    'GET /feed/deeds/d1': { body: makeDetail() },
    'GET /feed/deeds/d1/comments': page([makeComment(1)]),
    ...(handlers as Record<string, never>),
  })
}

const reporterDeed = (visibility: Visibility = LISTED) =>
  makeDetail({ my_role: 'reporter', reporter: PRIYA, visibility })

describe('DeedPage', () => {
  it('shows the card, the helped line and the comments', async () => {
    setup()
    renderApp('/deeds/d1')
    expect(await screen.findByRole('heading', { level: 1, name: 'Ahmed Khan' })).toBeInTheDocument()
    expect(
      screen.getByText('Helped 1 person · Checked by QR code and location'),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Comments (5)' })).toBeInTheDocument()
    expect(await screen.findByText('Comment 1')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Feed' })).toHaveAttribute('href', '/')
    // A member who isn't the doer or reporter sees no visibility row and no Delete.
    expect(screen.queryByText(/public feed/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete comment/ })).not.toBeInTheDocument()
  })

  it('hides the count part of the helped line when it is null', async () => {
    setup({ 'GET /feed/deeds/d1': { body: makeDetail({ beneficiaries_count: null }) } })
    renderApp('/deeds/d1')
    expect(await screen.findByText('Checked by QR code and location')).toBeInTheDocument()
  })

  it('says "Checked by invite link" for an invite deed', async () => {
    setup({ 'GET /feed/deeds/d1': { body: makeDetail({ verification: 'invite_accept' }) } })
    renderApp('/deeds/d1')
    expect(await screen.findByText('Helped 1 person · Checked by invite link')).toBeInTheDocument()
  })

  it('shows the unavailable state on 404', async () => {
    setup({
      'GET /feed/deeds/d1': { status: 404, body: { detail: 'Not found', code: 'deed_not_found' } },
    })
    renderApp('/deeds/d1')
    expect(await screen.findByRole('heading', { name: 'This deed isn’t available' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to the feed' })).toHaveAttribute('href', '/')
  })

  it('hides after the confirm dialog, with the agreed copy (Q24)', async () => {
    const api = setup({
      'GET /feed/deeds/d1': [{ body: reporterDeed() }, { body: reporterDeed(HIDDEN_BY_ME) }],
      'POST /feed/deeds/d1/hide': { body: HIDDEN_BY_ME },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    expect(
      await screen.findByText('You reported this deed. It’s in the public feed.'),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Hide from feed' }))
    const dialog = screen.getByRole('dialog', { name: 'Hide this deed from the feed?' })
    expect(dialog).toHaveTextContent(
      'It stays verified and still counts on Ahmed’s profile and for points. Its share link stops working. You can show it again at any time.',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Hide from feed' }))
    expect(await screen.findByText('Hidden from the feed', { selector: 'strong' })).toBeVisible()
    expect(
      screen.getByText(
        /Only you and Ahmed can see it\. It’s still verified and still counts on Ahmed’s profile\. Its share link is turned off\./,
      ),
    ).toBeInTheDocument()
    expect(api.calls('POST /feed/deeds/d1/hide')).toHaveLength(1)
    // A hidden deed has no share link.
    expect(screen.queryByRole('button', { name: 'Share' })).not.toBeInTheDocument()
  })

  it('Show in feed calls show', async () => {
    const api = setup({
      'GET /feed/deeds/d1': [{ body: reporterDeed(HIDDEN_BY_ME) }, { body: reporterDeed() }],
      'POST /feed/deeds/d1/show': { body: LISTED },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Show in feed' }))
    expect(await screen.findByText('It’s back in the public feed.')).toBeInTheDocument()
    expect(api.calls('POST /feed/deeds/d1/show')).toHaveLength(1)
  })

  it('names the other person when they hid it, with no button (Q24)', async () => {
    setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({
          my_role: 'doer',
          doer: PRIYA,
          reporter: AHMED,
          visibility: {
            ...HIDDEN_BY_ME,
            hidden_by_me: false,
            hidden_by_other: true,
            can_show: false,
          },
        }),
      },
    })
    renderApp('/deeds/d1')
    expect(await screen.findByText('Ahmed hid this deed from the feed')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Show in feed' })).not.toBeInTheDocument()
    expect(screen.getByText(/still counts on your profile/)).toBeInTheDocument()
  })

  it('shows Delete only where can_delete, with the reporter note, and confirms first', async () => {
    const api = setup({
      'GET /feed/deeds/d1': { body: reporterDeed() },
      'GET /feed/deeds/d1/comments': page([
        makeComment(1, { can_delete: true }),
        makeComment(2, { author: PRIYA, mine: true, can_delete: true }),
      ]),
      'DELETE /feed/comments/c1': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await screen.findByText('Comment 1')
    expect(
      screen.getByText(
        'You see Delete on every comment because you reported this deed. Ahmed sees it too.',
      ),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete comment by Rahul Verma' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete this comment?' })
    expect(dialog).toHaveTextContent(
      'It’s removed for everyone. As the reporter of this deed, you can delete any comment on it.',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Delete comment' }))
    await waitFor(() => expect(api.calls('DELETE /feed/comments/c1')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('uses the shorter delete copy for my own comment', async () => {
    setup({
      'GET /feed/deeds/d1/comments': page([
        makeComment(2, { author: PRIYA, mine: true, can_delete: true }),
      ]),
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Delete comment by Priya Sharma' }))
    expect(screen.getByRole('dialog')).toHaveTextContent(
      /^Delete this comment\?It’s removed for everyone\.Delete/,
    )
  })
})

describe('Comment composer', () => {
  it('posts a comment, clears the box and counts to the config limit', async () => {
    const api = setup({
      'POST /feed/deeds/d1/comments': { status: 201, body: makeComment(9, { mine: true }) },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    const box = await screen.findByRole('textbox', { name: 'Add a comment' })
    expect(await screen.findByText('0 / 300')).toBeInTheDocument()
    expect(box).toHaveAttribute('maxLength', '300')
    await user.type(box, '  So kind  ')
    expect(screen.getByText('11 / 300')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Post' }))
    await waitFor(() => expect(box).toHaveValue(''))
    expect(api.calls('POST /feed/deeds/d1/comments')[0].body).toEqual({ body: 'So kind' })
  })

  it.each([
    [
      'comment_not_kind',
      422,
      'Please keep it kind. Your comment includes a word we don’t allow. Try rewording it.',
    ],
    ['rate_limited', 429, 'You’re commenting very fast — try again in a minute'],
    ['comment_has_link', 422, 'Links aren’t allowed in comments.'],
    ['comment_has_phone', 422, 'Please leave out phone numbers.'],
  ])('shows %s inline', async (code, status, message) => {
    setup({ 'POST /feed/deeds/d1/comments': { status, body: { detail: 'x', code } } })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    const box = await screen.findByRole('textbox', { name: 'Add a comment' })
    await user.type(box, 'hello')
    await user.click(screen.getByRole('button', { name: 'Post' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(box).toHaveValue('hello')
  })
})

describe('Report comment (Q38)', () => {
  it('reports another person’s comment with a reason and details; the comment stays', async () => {
    const api = setup({
      'GET /feed/deeds/d1/comments': page([
        makeComment(1),
        makeComment(2, { author: PRIYA, mine: true, can_delete: true }),
      ]),
      'POST /feed/comments/c1/reports': { status: 202, body: { status: 'received' } },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await screen.findByText('Comment 1')
    expect(
      screen.queryByRole('button', { name: 'Report comment by Priya Sharma' }),
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Report comment by Rahul Verma' }))
    const sheet = screen.getByRole('dialog', { name: 'Report this comment' })
    const send = within(sheet).getByRole('button', { name: 'Send report' })
    expect(send).toBeDisabled()
    expect(within(sheet).getAllByRole('radio')).toHaveLength(5)
    await user.click(within(sheet).getByRole('radio', { name: 'Spam or scam' }))
    const details = within(sheet).getByRole('textbox', { name: 'Add details (optional)' })
    expect(details).toHaveAttribute('maxLength', '500')
    await user.type(details, ' Selling stuff ')
    await user.click(send)
    expect(
      await screen.findByText(
        'Thanks. Our team will review it. The comment stays up until they decide.',
      ),
    ).toBeInTheDocument()
    expect(api.calls('POST /feed/comments/c1/reports')[0].body).toEqual({
      reason: 'spam',
      details: 'Selling stuff',
    })
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.getByText('Comment 1')).toBeInTheDocument()
  })

  it('shows the rate-limit message', async () => {
    setup({
      'POST /feed/comments/c1/reports': {
        status: 429,
        body: { detail: 'x', code: 'rate_limited' },
      },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Report comment by Rahul Verma' }))
    await user.click(screen.getByRole('radio', { name: 'Hateful' }))
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You’ve sent a lot of reports. Please try again later.',
    )
  })
})

describe('Doer photo removal (Q29)', () => {
  const doerDeed = () =>
    makeDetail({
      my_role: 'doer',
      doer: PRIYA,
      reporter: AHMED,
      photos: [makePhoto(1), makePhoto(2), makePhoto(3)],
      visibility: LISTED,
    })

  it('removes the photo showing after the confirm, leaving the others with no placeholder', async () => {
    const api = setup({
      'GET /feed/deeds/d1': { body: doerDeed() },
      'DELETE /media/photos/p1': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    const carousel = await screen.findByRole('region', { name: 'Photos' })
    expect(within(carousel).getByText('1 / 3')).toBeInTheDocument()
    await user.click(within(carousel).getByRole('button', { name: 'Remove photo 1 of 3' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this photo?' })
    expect(dialog).toHaveTextContent(
      'It will be removed from the feed, this deed and its share link. Ahmed Khan can’t put it back. Your deed stays verified and your points don’t change.',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Remove photo' }))
    await waitFor(() => expect(api.calls('DELETE /media/photos/p1')).toHaveLength(1))
    expect(await screen.findByText('1 / 2')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /of 3/ })).not.toBeInTheDocument()
    expect(screen.getByText('Photo removed.')).toBeInTheDocument()
  })

  it('Keep it changes nothing, and only the doer gets the ✕', async () => {
    const api = setup({ 'GET /feed/deeds/d1': { body: doerDeed() } })
    const user = userEvent.setup()
    renderApp('/deeds/d1')
    await user.click(await screen.findByRole('button', { name: 'Remove photo 1 of 3' }))
    await user.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(screen.getByText('1 / 3')).toBeInTheDocument()
    expect(api.calls('DELETE /media/photos/p1')).toHaveLength(0)
  })

  it('shows no ✕ to the reporter or a member', async () => {
    setup({
      'GET /feed/deeds/d1': {
        body: makeDetail({
          my_role: 'reporter',
          reporter: PRIYA,
          photos: [makePhoto(1)],
          visibility: LISTED,
        }),
      },
    })
    renderApp('/deeds/d1')
    await screen.findByRole('img', { name: 'Photo 1 of 1' })
    expect(screen.queryByRole('button', { name: /Remove photo/ })).not.toBeInTheDocument()
  })
})
