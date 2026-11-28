/**
 * M6 F26/F27 on profiles (docs/M6-DESIGN.md §3.3 P6, §6.1, D113, D119, D121): the "⋯" menu on
 * someone's profile reports their picture (signed-in, onboarded, reasons with "Shows a child"
 * first) and mutes their notifications; visitors see only the default avatar; and the admin
 * photo-report queue filters to profile pictures and shows the owner instead of a deed.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'
import { makePhotoReportDetail, makePhotoReportItem, MOD } from '../admin/testFixtures'
import { makeMediaOptions } from '../media/testFixtures'
import { resetAvatarBatchForTests } from '../media/avatar/useAvatarUrls'
import { makeOptions } from '../report/testFixtures'

afterEach(() => resetAvatarBatchForTests())

const RAVI = {
  handle: 'ravi',
  display_name: 'Ravi Kumar',
  city: 'Bangalore',
  area: null,
  bio: null,
  joined_at: '2026-11-01T10:00:00Z',
  avatar: 'ref-ravi',
}

const URLS = {
  sm_url: 'https://storage.test/ravi/sm.webp',
  md_url: 'https://storage.test/ravi/md.webp',
  lg_url: 'https://storage.test/ravi/lg.webp',
  expires_at: '2099-01-01T00:00:00Z',
}

function profileHandlers(extra: Record<string, unknown> = {}) {
  return {
    'GET /users/ravi': { body: RAVI },
    'POST /media/avatars/urls': { body: { items: { 'ref-ravi': URLS } } },
    'GET /notifications/preferences': {
      body: { categories: [], mutes: { people: [], deeds: [] } },
    },
    ...extra,
  } as Parameters<typeof mockApi>[0]
}

describe('profile picture on a public profile', () => {
  it('shows the picture to members and reports it with a reason', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      ...profileHandlers({
        'GET /users/ravi/avatar-report-target': { body: { photo_id: 'ph-ravi' } },
        'POST /media/photos/ph-ravi/reports': { status: 202, body: { status: 'received' } },
      }),
    })
    const user = userEvent.setup()
    const { container } = renderApp('/u/ravi')
    await screen.findByRole('heading', { name: 'Ravi Kumar' })
    await waitFor(() =>
      expect(
        container.querySelector('img[src="https://storage.test/ravi/lg.webp"]'),
      ).not.toBeNull(),
    )
    await user.click(screen.getByRole('button', { name: 'More options for Ravi Kumar' }))
    await user.click(screen.getByRole('button', { name: 'Report profile picture' }))
    const sheet = screen.getByRole('dialog', { name: 'Report profile picture' })
    const reasons = within(sheet).getAllByRole('radio')
    expect(reasons[0]).toHaveAccessibleName('Shows a child')
    expect(
      within(sheet).getByRole('radio', { name: 'Not this person / impersonation' }),
    ).toBeInTheDocument()
    await user.click(within(sheet).getByRole('radio', { name: 'Not this person / impersonation' }))
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    expect(
      await screen.findByText(/Thanks. Someone from our team will look at it./),
    ).toBeInTheDocument()
    expect(api.calls('POST /media/photos/ph-ravi/reports')[0].body).toEqual({
      reason: 'impersonation',
    })
  })

  it('mutes the person from the menu, silently', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      ...profileHandlers({ 'PUT /notifications/mutes': { status: 204 } }),
    })
    const user = userEvent.setup()
    renderApp('/u/ravi')
    await user.click(await screen.findByRole('button', { name: 'More options for Ravi Kumar' }))
    await user.click(screen.getByRole('button', { name: 'Mute notifications from Ravi' }))
    await waitFor(() =>
      expect(api.calls('PUT /notifications/mutes')[0]?.body).toEqual({
        type: 'person',
        target: 'ravi',
      }),
    )
    expect(await screen.findByText(/Muted Ravi/)).toBeInTheDocument()
  })

  it('does not offer reporting before onboarding is complete', async () => {
    mockApi({
      ...signedIn(makeMe({ onboarding: NOT_ONBOARDED })),
      ...profileHandlers(),
    })
    const user = userEvent.setup()
    renderApp('/u/ravi')
    await user.click(await screen.findByRole('button', { name: 'More options for Ravi Kumar' }))
    expect(screen.queryByRole('button', { name: 'Report profile picture' })).toBeNull()
  })

  it('visitors see the default avatar and no menu (D119)', async () => {
    const api = mockApi(profileHandlers())
    const { container } = renderApp('/u/ravi')
    await screen.findByRole('heading', { name: 'Ravi Kumar' })
    expect(screen.queryByRole('button', { name: /More options/ })).toBeNull()
    expect(container.querySelector('main img')).toBeNull()
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(0)
  })
})

describe('admin photo reports: profile pictures', () => {
  const moderator = () => makeMe({ role: 'moderator', handle: MOD.handle })
  const OWNER = { handle: 'ravi', display_name: 'Ravi Kumar', avatar: 'ref-ravi' }

  it('filters the queue to profile pictures and shows the owner', async () => {
    const api = mockApi({
      ...signedIn(moderator()),
      'GET /deeds/options': { body: makeOptions() },
      'GET /media/options': { body: makeMediaOptions() },
      'POST /media/avatars/urls': { body: { items: {} } },
      'GET /admin/photo-reports': (req) => ({
        body: {
          items:
            req.query.get('purpose') === 'avatar'
              ? [
                  makePhotoReportItem({
                    case_id: 'c-av',
                    purpose: 'avatar',
                    deed: null,
                    owner: OWNER,
                    top_reason: 'impersonation',
                  }),
                ]
              : [makePhotoReportItem({ case_id: 'c-deed' })],
          next_cursor: null,
          open_count: 1,
          resolved_count: 0,
        },
      }),
    })
    const user = userEvent.setup()
    renderApp('/admin/photo-reports')
    await screen.findAllByRole('link', { name: /Review the report/ })
    await user.click(screen.getByRole('button', { name: 'Profile pictures' }))
    expect(
      await screen.findByRole('link', {
        name: 'Review the report on Ravi Kumar’s profile picture',
      }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Profile picture · Ravi Kumar/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Profile pictures' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(api.calls('GET /admin/photo-reports').at(-1)?.query.get('purpose')).toBe('avatar')
  })

  it('review page shows the owner instead of a deed, with picture wording', async () => {
    const detail = {
      ...makePhotoReportDetail(),
      purpose: 'avatar' as const,
      deed: null,
      owner: OWNER,
    }
    mockApi({
      ...signedIn(moderator()),
      'GET /deeds/options': { body: makeOptions() },
      'GET /media/options': { body: makeMediaOptions() },
      'POST /media/avatars/urls': { body: { items: {} } },
      'GET /admin/photo-reports/case-1': { body: detail },
    })
    renderApp('/admin/photo-reports/case-1')
    expect(await screen.findByText('Profile picture of')).toBeInTheDocument()
    expect(screen.queryByText('Deed')).toBeNull()
    expect(
      screen.getByRole('img', { name: 'Reported profile picture of Ravi Kumar' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Keep picture' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove picture' })).toBeInTheDocument()
  })
})
