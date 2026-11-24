/**
 * F16: the report-photo sheet (docs/M3-DESIGN.md §6.3 "Report a photo", §8): Send disabled until a
 * reason, details, the thank-you state, rate-limit copy, visitors on the public page, the menu
 * hiding Report photo for the uploader and on cards without photos, and the photo staying up.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn, type MockRequest } from '../../test/utils'
import { FEED_OPTIONS, makePhoto, makePost, makePublic, page } from '../feed/testFixtures'
import { makeMediaOptions } from './testFixtures'

const RAHUL = makeMe({ handle: 'rahulv', display_name: 'Rahul Verma' })

function feed(posts: ReturnType<typeof makePost>[], extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(RAHUL),
    'GET /feed/options': { body: FEED_OPTIONS },
    'GET /feed': (req: MockRequest) => (req.query.get('tab') === 'nearby' ? page(posts) : page([])),
    'GET /feed/me/notices': { body: [] },
    'GET /feed/me/comment-notices': { body: [] },
    'GET /media/options': { body: makeMediaOptions() },
    ...extra,
  })
}

async function openSheet(user = userEvent.setup()) {
  await user.click(await screen.findByRole('button', { name: 'More options for this deed' }))
  await user.click(screen.getByRole('button', { name: 'Report photo' }))
  return { user, sheet: screen.getByRole('dialog', { name: 'Report this photo' }) }
}

describe('report-photo sheet (member, feed card)', () => {
  it('shows the reasons, keeps Send disabled until one is picked, and thanks the reporter', async () => {
    const api = feed([makePost({ photos: [makePhoto(1), makePhoto(2)] })], {
      'POST /media/photos/p1/reports': { status: 202, body: { status: 'received' } },
    })
    renderApp('/')
    const { user, sheet } = await openSheet()
    const radios = within(sheet).getAllByRole('radio')
    expect(radios.map((r) => r.closest('label')?.textContent)).toEqual([
      'I’m in this photo and didn’t agree',
      'A child is shown without a parent’s consent',
      'It shows private details (like a phone number or address)',
      'It’s inappropriate or harmful',
      'Something else',
    ])
    expect(sheet).toHaveTextContent(
      'Our team reviews every report. The photo stays up until they decide. The person who posted it won’t see who reported it.',
    )
    const send = within(sheet).getByRole('button', { name: 'Send report' })
    expect(send).toBeDisabled()
    await user.click(
      within(sheet).getByRole('radio', { name: 'I’m in this photo and didn’t agree' }),
    )
    await user.type(within(sheet).getByRole('textbox', { name: /Add details/ }), '  My father  ')
    await user.click(send)

    const thanks = await screen.findByRole('heading', { name: 'Thanks for telling us' })
    expect(thanks).toHaveFocus()
    expect(screen.getByRole('dialog')).toHaveTextContent(
      'Our team will review this photo. Reports about children and privacy are looked at first.',
    )
    expect(api.calls('POST /media/photos/p1/reports')[0].body).toEqual({
      reason: 'not_consented',
      details: 'My father',
    })
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    // Reporting never hides the photo.
    expect(screen.getByRole('article', { name: 'Deed by Ahmed Khan' })).toBeInTheDocument()
  })

  it('sends no details when the box is empty', async () => {
    const api = feed([makePost({ photos: [makePhoto(1)] })], {
      'POST /media/photos/p1/reports': { status: 202, body: { status: 'received' } },
    })
    renderApp('/')
    const { user, sheet } = await openSheet()
    await user.click(within(sheet).getByRole('radio', { name: 'Something else' }))
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    await screen.findByRole('heading', { name: 'Thanks for telling us' })
    expect(api.calls('POST /media/photos/p1/reports')[0].body).toEqual({ reason: 'other' })
  })

  it('shows the rate-limit message and stays open', async () => {
    feed([makePost({ photos: [makePhoto(1)] })], {
      'POST /media/photos/p1/reports': {
        status: 429,
        body: { detail: 'x', code: 'rate_limited' },
      },
    })
    renderApp('/')
    const { user, sheet } = await openSheet()
    await user.click(within(sheet).getByRole('radio', { name: 'Something else' }))
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(
      'Too many reports from this connection. Please try again later.',
    )
  })

  it('Cancel and Esc close the sheet', async () => {
    feed([makePost({ photos: [makePhoto(1)] })])
    renderApp('/')
    const { user, sheet } = await openSheet()
    await user.click(within(sheet).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    await openSheet(user)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('the menu hides Report photo for the uploader and on cards without photos', async () => {
    feed([
      makePost({ deed_id: 'd1', photos: [makePhoto(1)], my_role: 'reporter' }),
      makePost({ deed_id: 'd2', photos: [] }),
    ])
    const user = userEvent.setup()
    renderApp('/')
    const menus = await screen.findAllByRole('button', { name: 'More options for this deed' })
    for (const menu of menus) {
      await user.click(menu)
      expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Report photo' })).toBeNull()
      await user.keyboard('{Escape}')
    }
  })
})

describe('report-photo sheet (visitor, public page)', () => {
  it('lets a visitor report with the built-in reasons, without loading media options', async () => {
    const api = mockApi({
      'GET /public/deeds/d1': { body: makePublic({ photos: [makePhoto(1)] }) },
      'POST /media/photos/p1/reports': { status: 202, body: { status: 'received' } },
    })
    renderApp('/d/d1')
    const { user, sheet } = await openSheet()
    expect(within(sheet).getAllByRole('radio')).toHaveLength(5)
    await user.click(
      within(sheet).getByRole('radio', { name: 'A child is shown without a parent’s consent' }),
    )
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    await screen.findByRole('heading', { name: 'Thanks for telling us' })
    expect(api.calls('POST /media/photos/p1/reports')[0].headers.has('Authorization')).toBe(false)
    await waitFor(() => expect(api.calls('GET /media/options')).toHaveLength(0))
  })
})
