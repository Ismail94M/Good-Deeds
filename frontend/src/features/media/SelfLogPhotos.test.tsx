/** F14: private photos on the self-log form (docs/M3-DESIGN.md §6.3 "Self-log form"). */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { SelfLogOut } from '../deeds/deedsApi'
import { makeOptions } from '../report/testFixtures'
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

const LOG: SelfLogOut = {
  id: 'log-1',
  category: 'animals',
  description: 'Fed the stray dogs on my street before work.',
  occurred_at: '2026-11-14T07:15:00+05:30',
  has_location: false,
  created_at: '2026-11-14T08:00:00+05:30',
  updated_at: '2026-11-14T08:00:00+05:30',
}

const TICKET = {
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
}

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(makeMe()),
    'GET /deeds/options': { body: makeOptions() },
    'GET /media/options': { body: makeMediaOptions() },
    'GET /me/self-logs': { body: { items: [LOG], next_cursor: null } },
    'GET /users/asha/deed-counts': { body: { verified: 0 } },
    'POST /media/uploads': TICKET,
    'PUT https://storage.test/staging/ph1': { status: 200 },
    'POST /media/uploads/ph1/complete': { body: { photo_id: 'ph1', status: 'uploaded' } },
    ...handlers,
  })
}

async function photoSection() {
  return screen.findByRole('region', { name: 'Photos (optional)' }, { timeout: 3000 })
}

describe('self-log photos', () => {
  it('has no blur tool and says only you can see them', async () => {
    setup()
    const user = userEvent.setup()
    renderApp('/me/self-logs/new')
    const section = await photoSection()
    expect(section).toHaveTextContent('Only you can see these photos.')
    await user.upload(screen.getByTestId('photo-input') as HTMLInputElement, jpeg())
    await within(section).findByRole('img', { name: 'Photo 1' })
    expect(within(section).queryByRole('button', { name: /Blur/ })).toBeNull()
  })

  it('creates the log, then attaches the photos with no regions', async () => {
    const api = setup({
      'POST /me/self-logs': { status: 201, body: { ...LOG, id: 'log-9' } },
      'POST /media/self-logs/log-9/photos': { body: { photos: [] } },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/self-logs/new')
    await photoSection()
    await user.click(screen.getByRole('button', { name: 'Animals' }))
    await user.type(
      screen.getByRole('textbox', { name: 'What did you do?' }),
      'Fed the stray dogs on my street.',
    )
    await user.upload(screen.getByTestId('photo-input') as HTMLInputElement, jpeg())
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs'))
    expect(api.calls('POST /media/self-logs/log-9/photos')[0].body).toEqual({
      photos: [{ photo_id: 'ph1', blur_regions: [] }],
    })
    expect(api.calls('POST /media/uploads')[0].body).toMatchObject({ purpose: 'self_log' })
  })

  it('shows existing photos on edit, counts them, and removes one after confirming', async () => {
    const api = setup({
      'GET /media/self-logs/log-1/photos': {
        body: { photos: [makeOwnerPhoto({ id: 'p1' }), makeOwnerPhoto({ id: 'p2' })] },
      },
      'DELETE /media/photos/p1': { status: 204 },
    })
    const user = userEvent.setup()
    renderApp('/me/self-logs/log-1/edit')
    const section = await photoSection()
    await waitFor(() => expect(section).toHaveTextContent('2 of 4'))
    await user.click(within(section).getByRole('button', { name: 'Remove photo 1' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this photo?' })
    expect(dialog).toHaveTextContent('It’s deleted for good.')
    await user.click(within(dialog).getByRole('button', { name: 'Remove photo' }))
    await waitFor(() => expect(api.calls('DELETE /media/photos/p1')).toHaveLength(1))
  })

  it('attaches new photos on edit even when nothing else changed', async () => {
    const api = setup({
      'GET /media/self-logs/log-1/photos': { body: { photos: [] } },
      'POST /media/self-logs/log-1/photos': { body: { photos: [] } },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/self-logs/log-1/edit')
    await photoSection()
    await user.upload(screen.getByTestId('photo-input') as HTMLInputElement, jpeg())
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs'))
    expect(api.calls('PATCH /me/self-logs/log-1')).toHaveLength(0)
    expect(api.calls('POST /media/self-logs/log-1/photos')).toHaveLength(1)
  })

  it('asks to retry or remove a failed photo before saving', async () => {
    const api = setup({
      'GET /media/self-logs/log-1/photos': { body: { photos: [] } },
      'PUT https://storage.test/staging/ph1': { status: 500 },
    })
    const user = userEvent.setup()
    renderApp('/me/self-logs/log-1/edit')
    await photoSection()
    await user.upload(screen.getByTestId('photo-input') as HTMLInputElement, jpeg())
    await screen.findByRole('button', { name: 'Retry photo 1' })
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    expect(
      await screen.findByText('A photo didn’t upload. Retry it or remove it, then save.'),
    ).toBeInTheDocument()
    expect(api.calls('POST /media/self-logs/log-1/photos')).toHaveLength(0)
  })

  it('opens the edit page with a notice when a new log saved without its photos', async () => {
    setup({
      'POST /me/self-logs': { status: 201, body: LOG },
      'POST /media/self-logs/log-1/photos': {
        status: 409,
        body: { detail: 'x', code: 'photo_not_uploaded' },
      },
      'GET /media/self-logs/log-1/photos': { body: { photos: [] } },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/self-logs/new')
    await photoSection()
    await user.click(screen.getByRole('button', { name: 'Animals' }))
    await user.type(
      screen.getByRole('textbox', { name: 'What did you do?' }),
      'Fed the stray dogs on my street.',
    )
    await user.upload(screen.getByTestId('photo-input') as HTMLInputElement, jpeg())
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs/log-1/edit'))
    expect(
      await screen.findByText(
        'Your self-log was saved, but the photos didn’t upload. Please add them again below.',
      ),
    ).toBeInTheDocument()
  })
})
