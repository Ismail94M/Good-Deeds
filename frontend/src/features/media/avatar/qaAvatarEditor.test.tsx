/**
 * QA13 (docs/M6-DESIGN.md §6.3 avatar editor, §8 Frontend, D116, Q95): the editor's boundaries
 * and error states beyond the happy path: the raw-size cap at exactly 25 MB, the 256 px minimum
 * on each side, server refusals (413, too small, crop invalid, switched off), the 24 h wait with
 * its time, the Uploading → Processing → done states, every worker rejection, keyboard-only
 * cropping (steps, Shift, clamping at the edges and at the 256 px zoom limit), the remove
 * confirm (keep, fail), and a 390 px phone layout with nothing wider than the screen.
 */

import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn, type Reply } from '../../../test/utils'
import { resizeImage } from '../resizeImage'
import { jpeg, stubObjectUrls } from '../testFixtures'
import { AVATAR_RAW_MAX_BYTES, COPY, formatWhen } from './AvatarEditor'
import type { MyAvatar } from './avatarApi'
import { AVATAR_MIN_SIDE } from './cropGeometry'
import { resetAvatarBatchForTests } from './useAvatarUrls'

vi.mock('../resizeImage', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../resizeImage')>()
  return { ...actual, resizeImage: vi.fn() }
})

const STORAGE = 'https://storage.test/gooddeeds/staging/av1'
const BYTES = 999

function resizedTo(width: number, height: number) {
  vi.mocked(resizeImage).mockImplementation(async () => ({
    blob: new Blob([new Uint8Array(BYTES)], { type: 'image/jpeg' }),
    width,
    height,
  }))
}

beforeEach(() => {
  resizedTo(2000, 1000)
  stubObjectUrls()
})
afterEach(() => resetAvatarBatchForTests())

const NONE: MyAvatar = { current: null, pending: null, removed_notice: null }
const LIVE: MyAvatar = {
  ...NONE,
  current: {
    sm_url: 'https://storage.test/a/sm.webp',
    md_url: 'https://storage.test/a/md.webp',
    lg_url: 'https://storage.test/a/lg.webp',
    expires_at: '2099-01-01T00:00:00Z',
  },
}

const TICKET: Reply = {
  status: 201,
  body: {
    photo_id: 'av1',
    upload: {
      url: STORAGE,
      method: 'PUT',
      headers: { 'Content-Type': 'image/jpeg', 'Content-Length': String(BYTES) },
    },
    expires_at: '2026-11-27T10:15:00Z',
  },
}

function setup(extra: Record<string, unknown> = {}, me = makeMe()) {
  return mockApi({
    ...signedIn(me),
    'GET /media/me/avatar': { body: NONE },
    'POST /media/uploads': TICKET,
    [`PUT ${STORAGE}`]: { status: 200 },
    'POST /media/uploads/av1/complete': { body: { photo_id: 'av1', status: 'uploaded' } },
    'PUT /media/me/avatar': { status: 202, body: { status: 'processing' } },
    'POST /media/avatars/urls': { body: { items: {} } },
    ...(extra as Record<string, Reply>),
  })
}

async function openEditor(button = 'Add photo', title = 'Add a profile photo') {
  const user = userEvent.setup()
  renderApp('/profile')
  await user.click(await screen.findByRole('button', { name: button }, { timeout: 4000 }))
  const dialog = await screen.findByRole('dialog', { name: title })
  return { user, dialog }
}

function pick(file: File = jpeg()) {
  fireEvent.change(screen.getByTestId('avatar-file'), { target: { files: [file] } })
}

function sized(bytes: number): File {
  const file = jpeg('big.jpg', 1)
  Object.defineProperty(file, 'size', { value: bytes })
  return file
}

type SavedCrop = { photo_id: string; crop: { x: number; y: number; size: number } }

describe('choosing a photo: type and size boundaries', () => {
  it('refuses a file one byte over 25 MB without decoding or uploading it', async () => {
    const api = setup()
    const { dialog } = await openEditor()
    pick(sized(AVATAR_RAW_MAX_BYTES + 1))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(COPY.tooLarge)
    expect(resizeImage).not.toHaveBeenCalled()
    expect(api.calls('POST /media/uploads')).toHaveLength(0)
  })

  it('accepts a file of exactly 25 MB', async () => {
    setup()
    await openEditor()
    pick(sized(AVATAR_RAW_MAX_BYTES))
    expect(await screen.findByTestId('crop-frame')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('needs 256 px on the shorter side: 255 is refused, 256 is fine', async () => {
    setup()
    const { dialog } = await openEditor()
    resizedTo(2000, AVATAR_MIN_SIDE - 1)
    pick()
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(COPY.tooSmall)
    expect(screen.queryByTestId('crop-frame')).toBeNull()
    resizedTo(AVATAR_MIN_SIDE, AVATAR_MIN_SIDE)
    pick()
    expect(await screen.findByTestId('crop-frame')).toBeInTheDocument()
    // A 256 px photo can't be zoomed: the kept square would drop below 256 px.
    expect(screen.getByRole('slider', { name: 'Zoom' })).toBeDisabled()
  })

  it('refuses HEIC and PDF by type; offers only the accepted types', async () => {
    setup()
    const { dialog } = await openEditor()
    expect(screen.getByTestId('avatar-file')).toHaveAttribute(
      'accept',
      'image/jpeg,image/png,image/webp',
    )
    pick(new File(['x'], 'a.heic', { type: 'image/heic' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(COPY.unsupported)
    pick(new File(['x'], 'a.pdf', { type: 'application/pdf' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(COPY.unsupported)
    expect(resizeImage).not.toHaveBeenCalled()
  })
})

describe('saving: server refusals', () => {
  it.each([
    ['POST /media/uploads', 413, 'too_large', COPY.tooLarge],
    ['POST /media/uploads', 409, 'avatars_disabled', COPY.disabled],
    ['PUT /media/me/avatar', 422, 'image_too_small', COPY.tooSmall],
    ['PUT /media/me/avatar', 422, 'crop_invalid', 'The crop doesn’t fit the photo.'],
    ['PUT /media/me/avatar', 429, 'rate_limited', COPY.tooMany],
  ])('%s %i %s → a plain message, and the crop stays', async (key, status, code, text) => {
    setup({ [key]: { status, body: { detail: 'No', code } } })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    // Back on the crop step with Save enabled again, so the person can retry.
    expect(screen.getByTestId('crop-frame')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('the 24 h wait shows the exact time it ends (IST)', async () => {
    setup({
      'PUT /media/me/avatar': {
        status: 409,
        body: { detail: 'Later', code: 'cooldown', available_at: '2026-11-28T10:30:00Z' },
      },
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    const alert = await screen.findByRole('alert')
    // 10:30 UTC is 4:00 pm in Bangalore.
    expect(formatWhen('2026-11-28T10:30:00Z')).toMatch(/28 Nov.*4:00\s?pm/i)
    expect(alert).toHaveTextContent(
      `You can add a new photo after ${formatWhen('2026-11-28T10:30:00Z')}.`,
    )
  })

  it('a network failure says to check the connection', async () => {
    setup({
      'POST /media/uploads': () => {
        throw new TypeError('Failed to fetch')
      },
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(COPY.failed)
  })
})

describe('after a removal by our team (Q95)', () => {
  it('shows when a new photo is allowed, and blocks both buttons until then', async () => {
    const until = '2099-11-28T08:00:00Z'
    setup({
      'GET /media/me/avatar': {
        body: {
          ...NONE,
          removed_notice: {
            removed_at: '2099-11-27T08:00:00Z',
            reason_label: 'Shows a child',
            upload_after: until,
          },
        },
      },
    })
    const { dialog } = await openEditor()
    expect(
      await within(dialog).findByText(
        new RegExp(`You can add a new photo after ${formatWhen(until)}`),
      ),
    ).toBeInTheDocument()
    expect(within(dialog).getByText(/removed: Shows a child/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Choose photo' })).toBeDisabled()
  })

  it('once the wait is over, the notice stays but uploading is allowed', async () => {
    setup({
      'GET /media/me/avatar': {
        body: {
          ...NONE,
          removed_notice: {
            removed_at: '2026-11-01T08:00:00Z',
            reason_label: 'Private details',
            upload_after: '2026-11-02T08:00:00Z',
          },
        },
      },
    })
    const { dialog } = await openEditor()
    expect(await within(dialog).findByText(/removed: Private details/)).toBeInTheDocument()
    expect(within(dialog).queryByText(/You can add a new photo after/)).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Choose photo' })).toBeEnabled()
  })
})

describe('processing states', () => {
  it('Uploading… then Processing… then done; buttons are locked meanwhile', async () => {
    let releasePut: () => void = () => {}
    const api = setup({
      'GET /media/me/avatar': [
        { body: NONE },
        { body: { ...NONE, pending: { status: 'processing' } } },
        { body: LIVE },
      ],
      'POST /media/uploads/av1/complete': () =>
        new Promise<Reply>((resolve) => {
          releasePut = () => resolve({ body: { photo_id: 'av1', status: 'uploaded' } })
        }),
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Uploading…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled()
    expect(screen.getByRole('slider', { name: 'Zoom' })).toBeDisabled()
    releasePut()
    expect(await screen.findByText('Processing…')).toBeInTheDocument()
    expect(
      await screen.findByText('Your profile photo is updated.', undefined, { timeout: 5000 }),
    ).toBeInTheDocument()
    expect(api.calls('GET /media/me/avatar').length).toBeGreaterThanOrEqual(3)
  })

  it.each([
    ['unsupported_type', COPY.unsupported],
    ['too_large', COPY.tooLarge],
    ['too_many_pixels', COPY.tooLarge],
    ['corrupt', 'We couldn’t read this photo. Try another one.'],
    // Not a connection problem: a neutral retry message (SEC6 follow-up).
    ['size_mismatch', COPY.mismatch],
  ])('a worker rejection "%s" is explained', async (reason, text) => {
    setup({
      'GET /media/me/avatar': [
        { body: NONE },
        { body: { ...NONE, pending: { status: 'rejected', reject_reason: reason } } },
      ],
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(screen.queryByText('Your profile photo is updated.')).toBeNull()
  })

  it('an upload finished during the wait after a removal says when to try again', async () => {
    // SEC6-L3: queued before an admin removal, rejected by the worker with `cooldown`.
    const until = '2026-11-28T10:30:00Z'
    setup({
      'GET /media/me/avatar': [
        { body: NONE },
        {
          body: {
            ...NONE,
            pending: { status: 'rejected', reject_reason: 'cooldown' },
            removed_notice: {
              removed_at: '2026-11-27T10:30:00Z',
              reason_label: 'Not this person / impersonation',
              upload_after: until,
            },
          },
        },
      ],
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      `You can add a new photo after ${formatWhen(until)}.`,
    )
    expect(screen.queryByText('Your profile photo is updated.')).toBeNull()
  })

  it('a storage size mismatch never blames the connection', async () => {
    setup({
      'POST /media/uploads/av1/complete': {
        status: 400,
        body: { detail: 'The photo didn’t upload completely. Try again.', code: 'size_mismatch' },
      },
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(COPY.mismatch)
    expect(alert).not.toHaveTextContent(/connection/i)
  })

  it('a failed job with no reason says it didn’t upload', async () => {
    setup({
      'GET /media/me/avatar': [
        { body: NONE },
        { body: { ...NONE, pending: { status: 'failed', reject_reason: null } } },
      ],
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(COPY.failed)
  })
})

describe('cropping with the keyboard only', () => {
  async function cropAndSave(keys: { key: string; shiftKey?: boolean }[]) {
    const api = setup({ 'GET /media/me/avatar': [{ body: NONE }, { body: LIVE }] })
    const { user } = await openEditor()
    pick()
    const frame = await screen.findByTestId('crop-frame')
    expect(frame).toHaveAttribute('tabindex', '0')
    expect(frame).toHaveAccessibleName(/Arrow keys move it, plus and minus zoom/)
    frame.focus()
    for (const k of keys) fireEvent.keyDown(frame, k)
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Your profile photo is updated.', undefined, { timeout: 4000 })
    return api.calls('PUT /media/me/avatar')[0].body as SavedCrop
  }

  it('starts centred: a 2000×1000 photo keeps the middle 1000 px square', async () => {
    const { crop } = await cropAndSave([])
    expect(crop).toEqual({ x: 0.25, y: 0, size: 1 })
  })

  // jsdom has no layout, so the frame counts as 280 px wide: one 12 px step moves the
  // 1000 px square by 12/280 of its side, i.e. 0.0214 of the 2000 px width.
  const ONE_STEP_LEFT = 0.25 - ((12 / 280) * 1000) / 2000

  it('an arrow key moves the square one step', async () => {
    const { crop } = await cropAndSave([{ key: 'ArrowLeft' }])
    expect(crop.x).toBeCloseTo(ONE_STEP_LEFT, 5)
    expect(crop.size).toBe(1)
  })

  it('Shift moves four steps: 3 × right then Shift+left is one step left', async () => {
    const { crop } = await cropAndSave([
      { key: 'ArrowRight' },
      { key: 'ArrowRight' },
      { key: 'ArrowRight' },
      { key: 'ArrowLeft', shiftKey: true },
    ])
    expect(crop.x).toBeCloseTo(ONE_STEP_LEFT, 5)
  })

  it('never leaves the photo: holding left stops at the edge, up/down at full height', async () => {
    const keys = Array.from({ length: 40 }, () => ({ key: 'ArrowLeft' }))
    const { crop } = await cropAndSave([...keys, { key: 'ArrowUp' }, { key: 'ArrowDown' }])
    expect(crop).toEqual({ x: 0, y: 0, size: 1 })
  })

  const zoomIn = Array.from({ length: 60 }, (_, i) => ({ key: i % 2 ? '+' : '=' }))

  it('+ and = zoom in, but never past a 256 px square', async () => {
    const { crop } = await cropAndSave(zoomIn)
    // 1000 px shorter side → the smallest kept square is 256 px = 0.256 of it.
    expect(crop.size).toBeCloseTo(AVATAR_MIN_SIDE / 1000, 5)
    expect(crop.size * 1000).toBeGreaterThanOrEqual(AVATAR_MIN_SIDE - 0.01)
  })

  it('− and _ zoom back out, never below the whole shorter side', async () => {
    const zoomOut = Array.from({ length: 60 }, (_, i) => ({ key: i % 2 ? '-' : '_' }))
    const { crop } = await cropAndSave([...zoomIn, ...zoomOut])
    expect(crop.size).toBe(1)
  })

  it('other keys do nothing', async () => {
    const { crop } = await cropAndSave([{ key: 'a' }, { key: 'Enter' }, { key: ' ' }])
    expect(crop).toEqual({ x: 0.25, y: 0, size: 1 })
  })
})

describe('remove with a confirm', () => {
  const me = makeMe({ avatar: 'ref-me' })

  it('"Keep it" cancels without deleting', async () => {
    const api = setup({ 'GET /media/me/avatar': { body: LIVE } }, me)
    const { user, dialog } = await openEditor('Change photo', 'Profile photo')
    await user.click(await within(dialog).findByRole('button', { name: 'Remove photo' }))
    await user.click(within(dialog).getByRole('button', { name: 'Keep it' }))
    expect(within(dialog).getByRole('button', { name: 'Remove photo' })).toBeInTheDocument()
    expect(api.calls('DELETE /media/me/avatar')).toHaveLength(0)
  })

  it('a failed delete keeps the sheet open with the error', async () => {
    setup(
      {
        'GET /media/me/avatar': { body: LIVE },
        'DELETE /media/me/avatar': { status: 500, body: { detail: 'Something went wrong.' } },
      },
      me,
    )
    const { user, dialog } = await openEditor('Change photo', 'Profile photo')
    await user.click(await within(dialog).findByRole('button', { name: 'Remove photo' }))
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Something went wrong.')
    expect(screen.getByRole('dialog', { name: 'Profile photo' })).toBeInTheDocument()
  })

  it('no Remove button without a current photo', async () => {
    setup()
    const { dialog } = await openEditor()
    await within(dialog).findByRole('button', { name: 'Choose photo' })
    expect(within(dialog).queryByRole('button', { name: 'Remove photo' })).toBeNull()
  })
})

describe('phone layout (390 px)', () => {
  /** Fixed widths in classes (`w-[480px]`, `min-w-96`…) or inline styles wider than `max`. */
  function tooWide(root: HTMLElement, max: number): string[] {
    const found: string[] = []
    for (const el of [root, ...root.querySelectorAll<HTMLElement>('*')]) {
      for (const cls of el.classList) {
        const px = /^(?:min-)?w-\[(\d+)px\]$/.exec(cls)
        const scale = /^(?:min-)?w-(\d+)$/.exec(cls)
        if (px && Number(px[1]) > max) found.push(cls)
        if (scale && Number(scale[1]) * 4 > max) found.push(cls)
      }
      const width = parseFloat(el.style.width)
      if (el.style.width.endsWith('px') && width > max) found.push(`style width ${width}px`)
    }
    return found
  }

  beforeEach(() => {
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390)
  })

  it('the choose and crop steps fit a 390 px screen', async () => {
    setup()
    const { dialog } = await openEditor()
    expect(tooWide(dialog, 390 - 32)).toEqual([])
    pick()
    await screen.findByTestId('crop-frame')
    const crop = screen.getByRole('dialog', { name: 'Crop your photo' })
    expect(tooWide(crop, 390 - 32)).toEqual([])
    // The crop square shrinks with the sheet (full width, at most 320 px).
    expect(screen.getByTestId('crop-frame').className).toMatch(/\bw-full\b/)
    expect(screen.getByTestId('crop-frame').className).toMatch(/max-w-\[320px\]/)
    // Buttons are at least 44 px tall for touch.
    for (const name of ['Back', 'Save']) {
      expect(screen.getByRole('button', { name }).className).toMatch(/min-h-1[12]/)
    }
  })
})
