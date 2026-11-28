/**
 * M6 F27 (docs/M6-DESIGN.md §3.3 P1–P4, §6.3, Q95): the profile photo editor from Profile:
 * choose → crop (keyboard and zoom) with a live preview → upload (P1 avatar, PUT, complete) →
 * PUT /media/me/avatar with the crop → processing → done; the error states (type, too small,
 * daily limit, cooldown, rejected by the worker); and Remove with a confirm.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn, type Reply } from '../../../test/utils'
import { resizeImage } from '../resizeImage'
import { jpeg, stubObjectUrls } from '../testFixtures'
import type { MyAvatar } from './avatarApi'
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
  current: {
    sm_url: 'https://storage.test/a/sm.webp',
    md_url: 'https://storage.test/a/md.webp',
    lg_url: 'https://storage.test/a/lg.webp',
    expires_at: '2099-01-01T00:00:00Z',
  },
  pending: null,
  removed_notice: null,
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

async function openEditor() {
  const user = userEvent.setup()
  renderApp('/profile')
  await user.click(await screen.findByRole('button', { name: 'Add photo' }))
  const dialog = await screen.findByRole('dialog', { name: 'Add a profile photo' })
  return { user, dialog }
}

function pick(file: File = jpeg()) {
  fireEvent.change(screen.getByTestId('avatar-file'), { target: { files: [file] } })
}

describe('avatar editor', () => {
  it('crops with the keyboard and zoom, previews, uploads and saves the crop', async () => {
    const api = setup({
      'GET /media/me/avatar': [{ body: NONE }, { body: { ...LIVE } }],
    })
    const { dialog } = await openEditor()
    expect(
      within(dialog).getByText(/Use a photo of you, or any picture without other people/),
    ).toBeInTheDocument()
    pick()
    const frame = await screen.findByTestId('crop-frame')
    expect(screen.getAllByTestId('avatar-preview')).toHaveLength(3)
    // Zoom in to 2x, then move the square left with the keyboard.
    fireEvent.change(screen.getByRole('slider', { name: 'Zoom' }), { target: { value: '2' } })
    frame.focus()
    fireEvent.keyDown(frame, { key: 'ArrowLeft' })
    fireEvent.keyDown(frame, { key: '+' })
    fireEvent.keyDown(frame, { key: '-' })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Your profile photo is updated.')).toBeInTheDocument()

    expect(api.calls('POST /media/uploads')[0].body).toEqual({
      purpose: 'avatar',
      content_type: 'image/jpeg',
      size_bytes: BYTES,
    })
    expect(api.calls(`PUT ${STORAGE}`)).toHaveLength(1)
    expect(api.calls('POST /media/uploads/av1/complete')).toHaveLength(1)
    const body = api.calls('PUT /media/me/avatar')[0].body as {
      photo_id: string
      crop: { x: number; y: number; size: number }
    }
    expect(body.photo_id).toBe('av1')
    // Zoom 2 on a 2000×1000 image keeps a 500 px square (half the shorter side), moved left.
    expect(body.crop.size).toBeCloseTo(0.5)
    expect(body.crop.y).toBeCloseTo(0.25)
    expect(body.crop.x).toBeLessThan(0.375)
    expect(body.crop.x).toBeGreaterThan(0.3)
    // The profile reloads `me` to pick up the new picture.
    await waitFor(() => expect(api.calls('GET /users/me').length).toBeGreaterThan(1))
  })

  it('refuses a wrong file type and a photo that is too small', async () => {
    setup()
    const { dialog } = await openEditor()
    pick(new File(['x'], 'doc.gif', { type: 'image/gif' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'This file type isn’t supported',
    )
    resizedTo(200, 300)
    pick()
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'This photo is too small: pick one at least 256 × 256.',
    )
    expect(screen.queryByTestId('crop-frame')).toBeNull()
  })

  it('asks a member who has not finished setup to verify first, instead of opening the editor', async () => {
    const api = setup(
      {},
      makeMe({ onboarding: { adult: true, consents: true, phone: false, complete: false } }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/profile')
    await user.click(await screen.findByRole('button', { name: 'Add photo' }))
    const prompt = await screen.findByRole('dialog', { name: 'Finish setting up' })
    expect(prompt).toHaveTextContent('verify your phone) to add a profile photo')
    expect(screen.queryByRole('dialog', { name: 'Add a profile photo' })).toBeNull()
    expect(api.calls('POST /media/uploads')).toHaveLength(0)
    await user.click(within(prompt).getByRole('button', { name: 'Finish setting up' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
  })

  it('explains a 403 onboarding_required from the upload in plain words', async () => {
    setup({
      'POST /media/uploads': {
        status: 403,
        body: {
          detail: 'Please finish setting up your account first.',
          code: 'onboarding_required',
        },
      },
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Finish setting up your account (verify your phone) to add a profile photo.',
    )
  })

  it('explains the daily limit and the 24 h wait', async () => {
    setup({
      'PUT /media/me/avatar': [
        { status: 429, body: { detail: 'Too many', code: 'rate_limited' } },
        {
          status: 409,
          body: {
            detail: 'Later',
            code: 'cooldown',
            available_at: '2026-11-28T10:30:00Z',
          },
        },
      ],
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You’ve changed your photo a lot today; try tomorrow.',
    )
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/You can add a new photo after 28 Nov/),
    )
  })

  it('shows why the worker refused a picture', async () => {
    setup({
      'GET /media/me/avatar': [
        { body: NONE },
        { body: { ...NONE, pending: { status: 'rejected', reject_reason: 'animated' } } },
      ],
    })
    const { user } = await openEditor()
    pick()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Moving pictures can’t be used.')
  })

  it('after an admin removal: explains why and waits until the cooldown ends', async () => {
    setup({
      'GET /media/me/avatar': {
        body: {
          ...NONE,
          removed_notice: {
            removed_at: '2026-11-27T08:00:00Z',
            reason_label: 'it didn’t follow our photo guidelines',
            upload_after: '2099-11-28T08:00:00Z',
          },
        },
      },
    })
    const { dialog } = await openEditor()
    expect(
      await within(dialog).findByText(/Your last profile picture was removed/),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: 'photo guidelines' })).toHaveAttribute(
      'href',
      '/photo-guidelines',
    )
    expect(within(dialog).getByRole('button', { name: 'Choose photo' })).toBeDisabled()
  })

  it('removes the current photo after a confirm', async () => {
    const api = setup(
      {
        'GET /media/me/avatar': { body: LIVE },
        'DELETE /media/me/avatar': { status: 204 },
      },
      makeMe({ avatar: 'ref-me' }),
    )
    const user = userEvent.setup()
    renderApp('/profile')
    await user.click(await screen.findByRole('button', { name: 'Change photo' }))
    const dialog = await screen.findByRole('dialog', { name: 'Profile photo' })
    await user.click(await within(dialog).findByRole('button', { name: 'Remove photo' }))
    expect(within(dialog).getByText(/It’s deleted straight away/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(api.calls('DELETE /media/me/avatar')).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})
