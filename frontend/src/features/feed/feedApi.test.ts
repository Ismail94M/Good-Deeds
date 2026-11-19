import { describe, expect, it } from 'vitest'
import { ApiError } from '../../lib/api'
import { mockApi } from '../../test/utils'
import {
  feedApi,
  feedKeys,
  type CommentNotice,
  type CommentOut,
  type FeedOptions,
  type PostDetail,
  type PostOut,
  type PublicDeed,
  type SocialOut,
  type Visibility,
} from './feedApi'

const AHMED = { handle: 'ahmed', display_name: 'Ahmed Khan' }
const PRIYA = { handle: 'priya', display_name: 'Priya Sharma' }
const PHOTO = {
  id: 'p1',
  thumb_url: 'https://storage.test/photos/p1/thumb.webp?sig=1',
  display_url: 'https://storage.test/photos/p1/display.webp?sig=1',
  width: 1080,
  height: 810,
  urls_expire_at: '2026-11-19T12:00:00Z',
}

const POST: PostOut = {
  deed_id: 'd1',
  doer: AHMED,
  reporter: PRIYA,
  category: 'animals',
  category_label: 'Animals',
  area_name: 'Koramangala',
  occurred_at: '2026-11-19T08:00:00Z',
  published_at: '2026-11-19T08:01:00Z',
  description: 'Fed a stray dog',
  photos: [PHOTO],
  like_count: 4,
  comment_count: 1,
  liked: false,
  saved: false,
  following_doer: false,
  my_role: null,
  rank: null,
  share_url: 'http://localhost:5173/d/d1',
}

const VISIBILITY: Visibility = {
  listed: false,
  hidden_by_me: true,
  hidden_by_other: false,
  can_hide: false,
  can_show: true,
}

const DETAIL: PostDetail = {
  ...POST,
  my_role: 'reporter',
  beneficiaries_count: 1,
  verification: 'qr_scan',
  visibility: { ...VISIBILITY, listed: true, hidden_by_me: false, can_hide: true, can_show: false },
}

const COMMENT: CommentOut = {
  id: 'c1',
  author: PRIYA,
  body: 'Lovely!',
  created_at: '2026-11-19T09:00:00Z',
  mine: true,
  can_delete: true,
}

const SOCIAL: SocialOut = { followers: 3, following: 1, is_following: true, is_me: false }

const page = <T>(items: T[], next_cursor: string | null = null) => ({ items, next_cursor })

/** The full URL fetch was called with for request `n` (mockApi strips the query string). */
function urlOf(api: ReturnType<typeof mockApi>, n: number): string {
  return String(api.spy.mock.calls[n][0])
}

describe('feedApi: options', () => {
  it('loads feed options', async () => {
    const options: FeedOptions = {
      comment_max_chars: 300,
      top_badge_ranks: 3,
      top_periods: [
        { id: 'week', days: 7 },
        { id: 'month', days: 30 },
        { id: 'year', days: 365 },
      ],
      comment_report_reasons: [{ id: 'hate', label: 'Hateful', priority: 2 }],
      report_details_max: 500,
    }
    mockApi({ 'GET /feed/options': { body: options } })
    await expect(feedApi.options()).resolves.toEqual(options)
    expect(feedKeys.options).toEqual(['feed', 'options'])
  })
})

describe('feedApi: feed', () => {
  it('loads each tab, sending period only with top', async () => {
    const api = mockApi({ 'GET /feed': { body: page([POST], 'c2') } })

    await expect(feedApi.feed({ tab: 'nearby' })).resolves.toEqual(page([POST], 'c2'))
    expect(urlOf(api, 0)).toBe('/api/feed?tab=nearby')

    await feedApi.feed({ tab: 'following', period: 'month', cursor: 'c2' })
    expect(urlOf(api, 1)).toBe('/api/feed?tab=following&cursor=c2')

    await feedApi.feed({ tab: 'top' })
    expect(urlOf(api, 2)).toBe('/api/feed?tab=top&period=week')

    await feedApi.feed({ tab: 'top', period: 'year', cursor: 'snap.4' })
    expect(urlOf(api, 3)).toBe('/api/feed?tab=top&period=year&cursor=snap.4')
  })

  it('surfaces 400 invalid_cursor (a Top cursor from another period)', async () => {
    mockApi({ 'GET /feed': { status: 400, body: { detail: 'x', code: 'invalid_cursor' } } })
    await expect(
      feedApi.feed({ tab: 'top', period: 'month', cursor: 'week-cursor' }),
    ).rejects.toMatchObject({ status: 400, code: 'invalid_cursor' })
  })

  it('gets a deed page, and surfaces 404 deed_not_found', async () => {
    mockApi({
      'GET /feed/deeds/d1': { body: DETAIL },
      'GET /feed/deeds/gone': { status: 404, body: { detail: 'x', code: 'deed_not_found' } },
    })
    await expect(feedApi.deed('d1')).resolves.toEqual(DETAIL)
    const error = await feedApi.deed('gone').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, code: 'deed_not_found' })
  })

  it('encodes ids in paths', async () => {
    const api = mockApi({ 'GET /feed/deeds/a%2Fb': { body: DETAIL } })
    await feedApi.deed('a/b')
    expect(urlOf(api, 0)).toBe('/api/feed/deeds/a%2Fb')
  })
})

describe('feedApi: likes, saves, shares, hide', () => {
  it('likes with PUT and unlikes with DELETE', async () => {
    const api = mockApi({
      'PUT /feed/deeds/d1/like': { body: { liked: true, like_count: 5 } },
      'DELETE /feed/deeds/d1/like': { body: { liked: false, like_count: 4 } },
    })
    await expect(feedApi.like('d1')).resolves.toEqual({ liked: true, like_count: 5 })
    await expect(feedApi.unlike('d1')).resolves.toEqual({ liked: false, like_count: 4 })
    expect(api.calls('PUT /feed/deeds/d1/like')[0].body).toBeUndefined()
  })

  it('saves with PUT and unsaves with DELETE', async () => {
    mockApi({
      'PUT /feed/deeds/d1/save': { body: { saved: true } },
      'DELETE /feed/deeds/d1/save': { body: { saved: false } },
    })
    await expect(feedApi.save('d1')).resolves.toEqual({ saved: true })
    await expect(feedApi.unsave('d1')).resolves.toEqual({ saved: false })
  })

  it('surfaces 403 onboarding_required on a write', async () => {
    mockApi({
      'PUT /feed/deeds/d1/like': {
        status: 403,
        body: { detail: 'Finish setting up first.', code: 'onboarding_required' },
      },
    })
    await expect(feedApi.like('d1')).rejects.toMatchObject({
      status: 403,
      code: 'onboarding_required',
    })
  })

  it('records a share', async () => {
    const api = mockApi({ 'POST /feed/deeds/d1/shares': { status: 204 } })
    await expect(feedApi.share('d1', 'whatsapp')).resolves.toBeUndefined()
    expect(api.calls('POST /feed/deeds/d1/shares')[0].body).toEqual({ target: 'whatsapp' })
  })

  it('surfaces 409 not_shareable for a hidden deed', async () => {
    mockApi({
      'POST /feed/deeds/d1/shares': { status: 409, body: { detail: 'x', code: 'not_shareable' } },
    })
    await expect(feedApi.share('d1', 'copy_link')).rejects.toMatchObject({
      code: 'not_shareable',
    })
  })

  it('hides and shows, returning the visibility', async () => {
    const shown = { ...VISIBILITY, listed: true, hidden_by_me: false, can_hide: true }
    mockApi({
      'POST /feed/deeds/d1/hide': { body: VISIBILITY },
      'POST /feed/deeds/d1/show': { body: shown },
    })
    await expect(feedApi.hide('d1')).resolves.toEqual(VISIBILITY)
    await expect(feedApi.show('d1')).resolves.toEqual(shown)
  })
})

describe('feedApi: comments', () => {
  it('lists comments with a cursor', async () => {
    const api = mockApi({ 'GET /feed/deeds/d1/comments': { body: page([COMMENT]) } })
    await expect(feedApi.comments('d1')).resolves.toEqual(page([COMMENT]))
    await feedApi.comments('d1', 'c9')
    expect(urlOf(api, 0)).toBe('/api/feed/deeds/d1/comments')
    expect(urlOf(api, 1)).toBe('/api/feed/deeds/d1/comments?cursor=c9')
  })

  it('posts a comment as {body}', async () => {
    const api = mockApi({ 'POST /feed/deeds/d1/comments': { status: 201, body: COMMENT } })
    await expect(feedApi.addComment('d1', 'Lovely!')).resolves.toEqual(COMMENT)
    expect(api.calls('POST /feed/deeds/d1/comments')[0].body).toEqual({ body: 'Lovely!' })
  })

  it.each(['comment_not_kind', 'comment_has_link', 'comment_has_phone', 'comment_too_long'])(
    'surfaces 422 %s',
    async (code) => {
      mockApi({ 'POST /feed/deeds/d1/comments': { status: 422, body: { detail: 'x', code } } })
      await expect(feedApi.addComment('d1', 'x')).rejects.toMatchObject({ status: 422, code })
    },
  )

  it('deletes a comment', async () => {
    const api = mockApi({ 'DELETE /feed/comments/c1': { status: 204 } })
    await expect(feedApi.deleteComment('c1')).resolves.toBeUndefined()
    expect(api.calls('DELETE /feed/comments/c1')).toHaveLength(1)
  })

  it('reports a comment', async () => {
    const api = mockApi({
      'POST /feed/comments/c1/reports': { status: 202, body: { status: 'received' } },
    })
    await expect(
      feedApi.reportComment('c1', { reason: 'personal_info', details: 'Has an address' }),
    ).resolves.toEqual({ status: 'received' })
    expect(api.calls('POST /feed/comments/c1/reports')[0].body).toEqual({
      reason: 'personal_info',
      details: 'Has an address',
    })
  })
})

describe('feedApi: public page', () => {
  const PUBLIC: PublicDeed = {
    deed_id: 'd1',
    doer: AHMED,
    reporter: PRIYA,
    category: 'animals',
    category_label: 'Animals',
    area_name: 'Koramangala',
    occurred_at: '2026-11-19T08:00:00Z',
    description: 'Fed a stray dog',
    photos: [],
    like_count: 4,
    comment_count: 1,
  }

  it('loads a public deed', async () => {
    mockApi({ 'GET /public/deeds/d1': { body: PUBLIC } })
    await expect(feedApi.publicDeed('d1')).resolves.toEqual(PUBLIC)
  })

  it('surfaces 404 deed_unavailable', async () => {
    mockApi({
      'GET /public/deeds/d1': { status: 404, body: { detail: 'x', code: 'deed_unavailable' } },
    })
    await expect(feedApi.publicDeed('d1')).rejects.toMatchObject({ code: 'deed_unavailable' })
  })
})

describe('feedApi: profiles and follows', () => {
  it('reads social counts and a person’s deeds', async () => {
    const api = mockApi({
      'GET /users/ahmed/social': { body: SOCIAL },
      'GET /feed/users/ahmed/deeds': { body: page([POST]) },
    })
    await expect(feedApi.social('ahmed')).resolves.toEqual(SOCIAL)
    await expect(feedApi.userDeeds('ahmed', 'c1')).resolves.toEqual(page([POST]))
    expect(urlOf(api, 1)).toBe('/api/feed/users/ahmed/deeds?cursor=c1')
  })

  it('follows with PUT and unfollows with DELETE, returning SocialOut', async () => {
    mockApi({
      'PUT /users/ahmed/follow': { body: SOCIAL },
      'DELETE /users/ahmed/follow': { body: { ...SOCIAL, followers: 2, is_following: false } },
    })
    await expect(feedApi.follow('ahmed')).resolves.toEqual(SOCIAL)
    await expect(feedApi.unfollow('ahmed')).resolves.toMatchObject({ is_following: false })
  })

  it.each([
    [400, 'cannot_follow_self'],
    [409, 'following_limit'],
    [404, 'user_not_found'],
  ])('surfaces %i %s on follow', async (status, code) => {
    mockApi({ 'PUT /users/ahmed/follow': { status, body: { detail: 'x', code } } })
    await expect(feedApi.follow('ahmed')).rejects.toMatchObject({ status, code })
  })

  it('removes a follower and lists my own connections', async () => {
    const row = { user: AHMED, following: false }
    const api = mockApi({
      'DELETE /me/followers/ahmed': { status: 204 },
      'GET /me/followers': { body: page([row]) },
      'GET /me/following': { body: page([], null) },
    })
    await expect(feedApi.removeFollower('ahmed')).resolves.toBeUndefined()
    await expect(feedApi.myFollowers()).resolves.toEqual(page([row]))
    await feedApi.myFollowing('c3')
    expect(urlOf(api, 2)).toBe('/api/me/following?cursor=c3')
  })

  it('lists saved deeds', async () => {
    const api = mockApi({ 'GET /me/saved': { body: page([{ ...POST, saved: true }]) } })
    const result = await feedApi.saved()
    expect(result.items[0].saved).toBe(true)
    expect(urlOf(api, 0)).toBe('/api/me/saved')
  })
})

describe('feedApi: notices', () => {
  it('lists unseen notice keys and marks one seen', async () => {
    const api = mockApi({
      'GET /feed/me/notices': { body: ['public_feed_intro'] },
      'POST /feed/me/notices/public_feed_intro/seen': { status: 204 },
    })
    await expect(feedApi.notices()).resolves.toEqual(['public_feed_intro'])
    await expect(feedApi.markNoticeSeen('public_feed_intro')).resolves.toBeUndefined()
    expect(api.calls('POST /feed/me/notices/public_feed_intro/seen')).toHaveLength(1)
  })

  it('lists comment-removed notices and marks one seen', async () => {
    const notice: CommentNotice = {
      comment_id: 'c1',
      deed_id: 'd1',
      doer: AHMED,
      excerpt: 'Something unkind',
      removed_at: '2026-11-19T10:00:00Z',
    }
    const api = mockApi({
      'GET /feed/me/comment-notices': { body: [notice] },
      'POST /feed/me/comment-notices/c1/seen': { status: 204 },
    })
    await expect(feedApi.commentNotices()).resolves.toEqual([notice])
    await feedApi.markCommentNoticeSeen('c1')
    expect(api.calls('POST /feed/me/comment-notices/c1/seen')).toHaveLength(1)
  })
})

describe('feedKeys', () => {
  it('follows the §6.4 key layout', () => {
    expect(feedKeys.list('nearby')).toEqual(['feed', 'nearby'])
    expect(feedKeys.list('following', 'year')).toEqual(['feed', 'following'])
    expect(feedKeys.list('top')).toEqual(['feed', 'top', 'week'])
    expect(feedKeys.list('top', 'month')).toEqual(['feed', 'top', 'month'])
    expect(feedKeys.deed('d1')).toEqual(['feed', 'deed', 'd1'])
    expect(feedKeys.comments('d1')).toEqual(['feed', 'comments', 'd1'])
    expect(feedKeys.social('Ahmed')).toEqual(['social', 'ahmed'])
  })
})
