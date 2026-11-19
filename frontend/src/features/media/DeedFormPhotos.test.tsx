/**
 * F14: photos on the deed form (docs/M3-DESIGN.md §6.3, §8 "Photos"): the picker only with
 * `photos_enabled`, the limit, upload with retry and type/size errors, the storage PUT without
 * Authorization or X-Device-Id, Send waiting for uploads, attach after send with regions, and the
 * result lines.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getPosition, permissionState } from '../../lib/geolocation'
import { makeMe, mockApi, renderApp, signedIn, type Reply } from '../../test/utils'
import { GOOD_FIX, makeDeed, makeOptions, makeScanDetail, SCAN_ID } from '../report/testFixtures'
import type { MediaOptions } from './mediaApi'
import { resizeImage } from './resizeImage'
import { jpeg, makeMediaOptions, RESIZED_BYTES, stubObjectUrls } from './testFixtures'

vi.mock('../../lib/geolocation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/geolocation')>()
  return { ...actual, getPosition: vi.fn(), permissionState: vi.fn() }
})

vi.mock('./resizeImage', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./resizeImage')>()
  return { ...actual, resizeImage: vi.fn() }
})

const PATH = `/report/scan/${SCAN_ID}`
const STORAGE = 'https://storage.test/gooddeeds/staging'

beforeEach(() => {
  vi.mocked(getPosition).mockReset().mockResolvedValue(GOOD_FIX)
  vi.mocked(permissionState).mockReset().mockResolvedValue('granted')
  vi.mocked(resizeImage)
    .mockReset()
    .mockImplementation(async () => ({
      blob: new Blob([new Uint8Array(RESIZED_BYTES)], { type: 'image/jpeg' }),
      width: 1600,
      height: 1200,
    }))
  stubObjectUrls()
})

let ticketN = 0

function ticketFor(): Reply {
  const id = `ph${++ticketN}`
  return {
    status: 201,
    body: {
      photo_id: id,
      upload: {
        url: `${STORAGE}/${id}?X-Amz-Signature=abc`,
        method: 'PUT',
        headers: { 'Content-Type': 'image/jpeg', 'Content-Length': String(RESIZED_BYTES) },
      },
      expires_at: '2026-11-19T10:15:00Z',
    },
  }
}

function setup({
  media = makeMediaOptions(),
  extra = {},
}: {
  media?: MediaOptions
  extra?: Parameters<typeof mockApi>[0]
} = {}) {
  ticketN = 0
  const handlers: Parameters<typeof mockApi>[0] = {
    ...signedIn(makeMe({ display_name: 'Priya Sharma' })),
    'GET /deeds/options': { body: makeOptions() },
    [`GET /scans/${SCAN_ID}`]: { body: makeScanDetail() },
    'GET /media/options': { body: media },
    'POST /media/uploads': () => ticketFor(),
    'POST /deeds': { status: 201, body: makeDeed({ reported_at: new Date().toISOString() }) },
    'POST /media/deeds/deed-1/photos': { body: { photos: [] } },
    'DELETE /media/uploads/ph1': { status: 204 },
    ...extra,
  }
  for (let i = 1; i <= 6; i++) {
    handlers[`PUT ${STORAGE}/ph${i}`] ??= { status: 200 }
    handlers[`POST /media/uploads/ph${i}/complete`] ??= {
      body: { photo_id: `ph${i}`, status: 'uploaded' },
    }
  }
  return mockApi(handlers)
}

async function fillValid(user = userEvent.setup()) {
  await user.click(await screen.findByRole('button', { name: 'Helping an elderly person' }))
  await user.type(
    screen.getByRole('textbox', { name: 'What did they do?' }),
    'Helped an elderly man cross the road.',
  )
  return user
}

/** The section appears once `/media/options` has loaded, after the form itself. */
async function photoSection() {
  await screen.findByRole('form', { name: 'Describe the deed' })
  return screen.findByRole('region', { name: 'Photos (optional)' }, { timeout: 3000 })
}

function fileInput(): HTMLInputElement {
  return screen.getByTestId('photo-input') as HTMLInputElement
}

describe('deed form photos', () => {
  it('shows no photo section when photos are switched off', async () => {
    setup({ media: makeMediaOptions({ photos_enabled: false }) })
    renderApp(PATH)
    await screen.findByRole('form', { name: 'Describe the deed' })
    expect(screen.queryByRole('heading', { name: /Photos/ })).toBeNull()
    expect(document.querySelector('input[type="file"]')).toBeNull()
  })

  it('shows the section with count, Add tile, accept list and help', async () => {
    setup()
    renderApp(PATH)
    const section = await photoSection()
    expect(section).toHaveTextContent('0 of 4')
    expect(within(section).getByRole('button', { name: 'Add photo' })).toBeInTheDocument()
    expect(fileInput()).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp')
    expect(section).toHaveTextContent(
      'Good deeds are meant to be shared proudly. If someone would rather not be shown, blur their face or remove the photo.',
    )
  })

  it('uploads a photo: create, PUT with only the signed type, complete', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderApp(PATH)
    await photoSection()
    await user.upload(fileInput(), jpeg())

    await waitFor(() => expect(api.calls('POST /media/uploads/ph1/complete')).toHaveLength(1))
    expect(api.calls('POST /media/uploads')[0].body).toEqual({
      purpose: 'deed',
      content_type: 'image/jpeg',
      size_bytes: RESIZED_BYTES,
    })
    const put = api.calls(`PUT ${STORAGE}/ph1`)[0]
    expect(put.headers.get('Content-Type')).toBe('image/jpeg')
    expect(put.headers.has('Authorization')).toBe(false)
    expect(put.headers.has('X-Device-Id')).toBe(false)
    expect(await screen.findByRole('img', { name: 'Photo 1' })).toHaveAttribute(
      'src',
      'blob:preview-1',
    )
    expect(screen.getByRole('region', { name: 'Photos (optional)' })).toHaveTextContent('1 of 4')
  })

  it('hides Add at the photo limit and ignores extra files with a notice', async () => {
    setup({ media: makeMediaOptions({ photos_per_owner: 2 }) })
    const user = userEvent.setup()
    renderApp(PATH)
    await photoSection()
    await user.upload(fileInput(), [jpeg('a.jpg'), jpeg('b.jpg'), jpeg('c.jpg')])
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Only 2 more photos fit, so we added the first 2.',
    )
    expect(screen.queryByRole('button', { name: 'Add photo' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Photos (optional)' })).toHaveTextContent('2 of 2')
  })

  it('refuses a file of the wrong type without uploading it', async () => {
    const api = setup()
    const user = userEvent.setup({ applyAccept: false })
    renderApp(PATH)
    await photoSection()
    await user.upload(fileInput(), new File(['GIF89a'], 'a.gif', { type: 'image/gif' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That file can’t be used. Choose a JPG, PNG or WebP photo under 10 MB.',
    )
    expect(api.calls('POST /media/uploads')).toHaveLength(0)
    expect(screen.queryByRole('img', { name: 'Photo 1' })).toBeNull()
  })

  it('refuses a photo still over the size limit after resizing', async () => {
    setup({ media: makeMediaOptions({ upload_max_bytes: 1000 }) })
    const user = userEvent.setup()
    renderApp(PATH)
    await photoSection()
    await user.upload(fileInput(), jpeg())
    expect(await screen.findByRole('alert')).toHaveTextContent('under 1 MB')
  })

  it('shows Retry when storage refuses the PUT, and retrying uploads it', async () => {
    const api = setup({ extra: { [`PUT ${STORAGE}/ph1`]: { status: 403 } } })
    const user = userEvent.setup()
    renderApp(PATH)
    await photoSection()
    await user.upload(fileInput(), jpeg())
    const retry = await screen.findByRole('button', { name: 'Retry photo 1' })
    expect(screen.getByRole('alert')).toHaveTextContent('This photo didn’t upload.')
    await user.click(retry)
    await waitFor(() => expect(api.calls('POST /media/uploads/ph2/complete')).toHaveLength(1))
    // The first, never-completed upload is discarded.
    await waitFor(() => expect(api.calls('DELETE /media/uploads/ph1')).toHaveLength(1))
    expect(screen.queryByRole('button', { name: 'Retry photo 1' })).toBeNull()
  })

  it('removing a photo discards its staged upload', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderApp(PATH)
    await photoSection()
    await user.upload(fileInput(), jpeg())
    await waitFor(() => expect(api.calls('POST /media/uploads/ph1/complete')).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Remove photo 1' }))
    await waitFor(() => expect(api.calls('DELETE /media/uploads/ph1')).toHaveLength(1))
    expect(screen.queryByRole('img', { name: 'Photo 1' })).toBeNull()
  })

  it('attaches the photos with their blur regions after the deed is sent', async () => {
    const api = setup()
    renderApp(PATH)
    const user = await fillValid()
    await user.upload(fileInput(), jpeg())
    await waitFor(() => expect(api.calls('POST /media/uploads/ph1/complete')).toHaveLength(1))

    await user.click(screen.getByRole('button', { name: 'Blur a face in photo 1' }))
    const editor = screen.getByRole('dialog', { name: 'Blur a face (optional)' })
    await user.click(within(editor).getByRole('button', { name: 'Done' }))
    expect(screen.getByText('Blurred')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit blur on photo 1' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Deed verified' })).toBeInTheDocument()
    const attach = api.calls('POST /media/deeds/deed-1/photos')
    expect(attach).toHaveLength(1)
    const [photo] = (attach[0].body as { photos: { photo_id: string; blur_regions: unknown[] }[] })
      .photos
    expect(photo.photo_id).toBe('ph1')
    // One centred circle on a 1600 × 1200 photo: 30 % of the short side.
    expect(photo.blur_regions).toEqual([{ x: 0.3875, y: 0.35, w: 0.225, h: 0.3 }])
    expect(screen.getByText('Your photos will appear in a few moments.')).toBeInTheDocument()
  })

  it('waits for running uploads before sending ("Uploading photos…")', async () => {
    let finish: () => void = () => {}
    const api = setup({
      extra: {
        'POST /media/uploads/ph1/complete': () =>
          new Promise<Reply>((resolve) => {
            finish = () => resolve({ body: { photo_id: 'ph1', status: 'uploaded' } })
          }),
      },
    })
    renderApp(PATH)
    const user = await fillValid()
    await user.upload(fileInput(), jpeg())
    await waitFor(() => expect(api.calls('POST /media/uploads/ph1/complete')).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('button', { name: 'Uploading photos…' })).toBeDisabled()
    expect(api.calls('POST /deeds')).toHaveLength(0)

    finish()
    expect(await screen.findByRole('heading', { name: 'Deed verified' })).toBeInTheDocument()
    expect(api.calls('POST /deeds')).toHaveLength(1)
    expect(api.calls('POST /media/deeds/deed-1/photos')).toHaveLength(1)
  })

  it('tells the witness to add photos from My deeds when attaching fails', async () => {
    const api = setup({
      extra: {
        'POST /media/deeds/deed-1/photos': {
          status: 409,
          body: { detail: 'x', code: 'photo_window_closed' },
        },
      },
    })
    renderApp(PATH)
    const user = await fillValid()
    await user.upload(fileInput(), jpeg())
    await waitFor(() => expect(api.calls('POST /media/uploads/ph1/complete')).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Deed verified' })).toBeInTheDocument()
    expect(
      screen.getByText(
        /^Your deed was sent, but the photos didn’t upload\. You can add them from My deeds until .+ tomorrow\.$/,
      ),
    ).toBeInTheDocument()
  })

  it('sends without photos when /media/options fails to load', async () => {
    const api = setup({ extra: { 'GET /media/options': { status: 500, body: { detail: 'x' } } } })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Deed verified' })).toBeInTheDocument()
    expect(api.calls('POST /media/deeds/deed-1/photos')).toHaveLength(0)
    expect(screen.queryByText(/photos/)).toBeNull()
  })
})
