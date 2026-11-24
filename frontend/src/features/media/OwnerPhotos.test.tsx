/**
 * F14 (+ outcome banners): photos on My deeds detail `/me/deeds/:id` (docs/M3-DESIGN.md §6.3):
 * the grid, remove at any time for the uploader (Q30) and for the doer (Q29), add inside the
 * window, the window-closed line, processing/failed tiles and the review outcome banners.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { DeedOut } from '../deeds/deedsApi'
import { AHMED, makeDeed, makeOptions } from '../report/testFixtures'
import type { DeedPhotosOut, MediaOptions } from './mediaApi'
import { resizeImage } from './resizeImage'
import {
  jpeg,
  makeMediaOptions,
  makeOwnerPhoto,
  RESIZED_BYTES,
  stubObjectUrls,
} from './testFixtures'

vi.mock('./resizeImage', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./resizeImage')>()
  return { ...actual, resizeImage: vi.fn() }
})

beforeEach(() => {
  vi.mocked(resizeImage)
    .mockReset()
    .mockImplementation(async () => ({
      blob: new Blob([new Uint8Array(RESIZED_BYTES)], { type: 'image/jpeg' }),
      width: 1600,
      height: 1200,
    }))
  stubObjectUrls()
})

const PATH = '/me/deeds/deed-1'
const ASHA = { handle: 'asha', display_name: 'Asha' }

function photosOut(overrides: Partial<DeedPhotosOut> = {}): DeedPhotosOut {
  return {
    photos: [makeOwnerPhoto({ id: 'p1', position: 1 }), makeOwnerPhoto({ id: 'p2', position: 2 })],
    can_add: true,
    add_until: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
    outcomes: [],
    ...overrides,
  }
}

function setup({
  me = makeMe(),
  deed = makeDeed({ reporter: ASHA }),
  photos = photosOut(),
  media = makeMediaOptions(),
  extra = {},
}: {
  me?: ReturnType<typeof makeMe>
  deed?: DeedOut
  photos?: DeedPhotosOut | DeedPhotosOut[]
  media?: MediaOptions
  extra?: Parameters<typeof mockApi>[0]
} = {}) {
  return mockApi({
    ...signedIn(me),
    'GET /deeds/options': { body: makeOptions() },
    'GET /deeds/deed-1': { body: deed },
    'GET /media/options': { body: media },
    'GET /media/deeds/deed-1/photos': Array.isArray(photos)
      ? photos.map((body) => ({ body }))
      : { body: photos },
    'DELETE /media/photos/p1': { status: 204 },
    ...extra,
  })
}

async function photosSection() {
  return screen.findByRole('region', { name: 'Photos' }, { timeout: 3000 })
}

describe('My deeds detail photos (reporter)', () => {
  it('shows the grid with alt text and the add-until line', async () => {
    setup()
    renderApp(PATH)
    const section = await photosSection()
    const images = within(section).getAllByRole('img', { name: /^Photo \d of 2$/ })
    expect(images).toHaveLength(2)
    expect(images[0]).toHaveAttribute('src', 'https://storage.test/photos/p1/thumb.webp?sig=1')
    expect(section).toHaveTextContent(
      /You can add photos until .+\. You can remove your photos at any time\./,
    )
    expect(within(section).getByRole('region', { name: 'Add photos' })).toHaveTextContent('2 of 4')
  })

  it('after the window: no Add, and the remove-any-time line', async () => {
    setup({ photos: photosOut({ can_add: false, add_until: null }) })
    renderApp(PATH)
    const section = await photosSection()
    expect(section).toHaveTextContent('You can remove your photos at any time.')
    expect(section).not.toHaveTextContent('You can add photos')
    expect(screen.queryByRole('button', { name: 'Add photo' })).toBeNull()
    // Removing still works after the window (Q30).
    expect(within(section).getByRole('button', { name: 'Remove photo 1' })).toBeInTheDocument()
  })

  it('removes a photo after confirming, then reloads the list', async () => {
    const api = setup({
      photos: [photosOut(), photosOut({ photos: [makeOwnerPhoto({ id: 'p2' })] })],
    })
    const user = userEvent.setup()
    renderApp(PATH)
    const section = await photosSection()
    await user.click(within(section).getByRole('button', { name: 'Remove photo 1' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this photo?' })
    expect(dialog).toHaveTextContent('It’s deleted for everyone.')
    await user.click(within(dialog).getByRole('button', { name: 'Remove photo' }))
    await waitFor(() => expect(api.calls('DELETE /media/photos/p1')).toHaveLength(1))
    await waitFor(() =>
      expect(screen.getAllByRole('img', { name: /^Photo \d of \d$/ })).toHaveLength(1),
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Keep it closes the confirm without removing', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderApp(PATH)
    const section = await photosSection()
    await user.click(within(section).getByRole('button', { name: 'Remove photo 1' }))
    await user.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.calls('DELETE /media/photos/p1')).toHaveLength(0)
  })

  it('shows processing and failed tiles', async () => {
    setup({
      photos: photosOut({
        photos: [
          makeOwnerPhoto({ id: 'p1', status: 'processing', thumb_url: null, display_url: null }),
          makeOwnerPhoto({ id: 'p2', status: 'failed', thumb_url: null, display_url: null }),
        ],
      }),
    })
    renderApp(PATH)
    const section = await photosSection()
    expect(section).toHaveTextContent('Preparing photo…')
    expect(section).toHaveTextContent('This photo couldn’t be prepared. Remove it and try again.')
  })

  it('adds photos from My deeds inside the window', async () => {
    const api = setup({
      extra: {
        'POST /media/uploads': {
          status: 201,
          body: {
            photo_id: 'ph1',
            upload: {
              url: 'https://storage.test/staging/ph1?sig=1',
              method: 'PUT',
              headers: { 'Content-Type': 'image/jpeg', 'Content-Length': String(RESIZED_BYTES) },
            },
            expires_at: '2026-11-19T10:15:00Z',
          },
        },
        'PUT https://storage.test/staging/ph1': { status: 200 },
        'POST /media/uploads/ph1/complete': { body: { photo_id: 'ph1', status: 'uploaded' } },
        'POST /media/deeds/deed-1/photos': { body: { photos: [] } },
      },
    })
    const user = userEvent.setup()
    renderApp(PATH)
    await photosSection()
    await user.upload(screen.getByTestId('photo-input') as HTMLInputElement, jpeg())
    await user.click(await screen.findByRole('button', { name: 'Add 1 photo to this deed' }))
    await waitFor(() => expect(api.calls('POST /media/deeds/deed-1/photos')).toHaveLength(1))
    expect(api.calls('POST /media/deeds/deed-1/photos')[0].body).toEqual({
      photos: [{ photo_id: 'ph1', blur_regions: [] }],
    })
  })

  it('still lists and removes photos when photos are switched off, without Add', async () => {
    setup({ media: makeMediaOptions({ photos_enabled: false }) })
    renderApp(PATH)
    const section = await photosSection()
    expect(within(section).getAllByRole('img')).toHaveLength(2)
    expect(within(section).getByRole('button', { name: 'Remove photo 2' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add photo' })).toBeNull()
  })

  it('renders nothing when the photo list is unavailable', async () => {
    setup({ extra: { 'GET /media/deeds/deed-1/photos': { status: 404, body: { detail: 'x' } } } })
    renderApp(PATH)
    await screen.findByText('Helped an elderly man cross the road.')
    expect(screen.queryByRole('region', { name: 'Photos' })).toBeNull()
  })
})

describe('My deeds detail photos (doer, Q29)', () => {
  it('offers ✕ on every ready photo with the final-removal copy, and no Add', async () => {
    setup({
      me: makeMe({ handle: AHMED.handle, display_name: AHMED.display_name }),
      deed: makeDeed({ reporter: { handle: 'priya', display_name: 'Priya Sharma' } }),
      photos: photosOut({ can_add: false, add_until: null }),
    })
    const user = userEvent.setup()
    renderApp(PATH)
    const section = await photosSection()
    expect(section).not.toHaveTextContent('You can remove your photos at any time.')
    await user.click(within(section).getByRole('button', { name: 'Remove photo 2' }))
    expect(screen.getByRole('dialog', { name: 'Remove this photo?' })).toHaveTextContent(
      'It will be removed from the feed, this deed and its share link. Priya Sharma can’t put it back. Your deed stays verified and your points don’t change.',
    )
  })
})

describe('review outcome banners (reporter only)', () => {
  const decided = '2026-11-18T10:00:00Z'

  it('removed: the reason text and the guidelines link', async () => {
    setup({
      photos: photosOut({
        outcomes: [
          {
            decision: 'removed',
            removal_reason: 'not_consented',
            removed_by: null,
            decided_at: decided,
            photo_count: 1,
          },
        ],
      }),
    })
    renderApp(PATH)
    const banner = await screen.findByText('A photo was removed after review', undefined, {
      timeout: 3000,
    })
    expect(banner.parentElement).toHaveTextContent(
      'Our team removed 1 photo because someone shown in it hadn’t agreed to appear. Your deed is still verified, and Ahmed’s count and points haven’t changed.',
    )
    expect(screen.getByRole('link', { name: 'Read the photo guidelines' })).toHaveAttribute(
      'href',
      '/photo-guidelines',
    )
  })

  it.each([
    ['child_no_guardian_consent', 'it showed a child without a parent’s consent'],
    ['private_details', 'it showed private details'],
    ['inappropriate', 'it didn’t follow our photo guidelines'],
    ['other', 'it didn’t follow our photo guidelines'],
  ] as const)('removed for %s says "%s"', async (reason, text) => {
    setup({
      photos: photosOut({
        outcomes: [
          {
            decision: 'removed',
            removal_reason: reason,
            removed_by: null,
            decided_at: decided,
            photo_count: 2,
          },
        ],
      }),
    })
    renderApp(PATH)
    const banner = await screen.findByText('2 photos were removed after review', undefined, {
      timeout: 3000,
    })
    expect(banner.parentElement).toHaveTextContent(`Our team removed 2 photos because ${text}.`)
  })

  it('kept and removed by the doer', async () => {
    setup({
      photos: photosOut({
        outcomes: [
          {
            decision: 'removed_by_doer',
            removal_reason: null,
            removed_by: AHMED,
            decided_at: decided,
            photo_count: 1,
          },
          {
            decision: 'kept',
            removal_reason: null,
            removed_by: null,
            decided_at: decided,
            photo_count: 1,
          },
        ],
      }),
    })
    renderApp(PATH)
    expect(
      await screen.findByText('Ahmed Khan removed 1 photo from this deed.', undefined, {
        timeout: 3000,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText('Review complete: your photos stay').parentElement).toHaveTextContent(
      'Someone reported a photo on this deed. Our team reviewed it and it stays up. No action needed.',
    )
  })

  it('never shows outcomes to the doer', async () => {
    setup({
      me: makeMe({ handle: AHMED.handle, display_name: AHMED.display_name }),
      photos: photosOut({
        can_add: false,
        outcomes: [
          {
            decision: 'kept',
            removal_reason: null,
            removed_by: null,
            decided_at: decided,
            photo_count: 1,
          },
        ],
      }),
    })
    renderApp(PATH)
    await photosSection()
    expect(screen.queryByText('Review complete: your photos stay')).toBeNull()
  })
})
