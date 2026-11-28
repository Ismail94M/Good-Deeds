/**
 * QA13 (docs/M6-DESIGN.md §3.3 P6, §6.3, D121, PO Q87/Q94): reporting someone's profile picture.
 * Only onboarded members, only on someone else's profile, only when they have a picture; the
 * reasons come from the server in its order ("Shows a child" first, impersonation included);
 * Send needs a reason; details are trimmed; 404 and 429 are explained.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'
import { AVATAR_REPORT_REASONS } from '../media/avatar/avatarApi'
import { resetAvatarBatchForTests } from '../media/avatar/useAvatarUrls'
import type { ReportReasonOption } from '../media/mediaApi'
import { makeMediaOptions } from '../media/testFixtures'

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

/** As the server sends them (media `_avatars.AVATAR_REPORT_REASON_LABELS`). */
const SERVER_REASONS: ReportReasonOption[] = [
  { id: 'child_no_guardian_consent', label: 'Shows a child', priority: 1 },
  { id: 'impersonation', label: 'Not this person / impersonation', priority: 2 },
  { id: 'private_details', label: 'Private details', priority: 2 },
  { id: 'inappropriate', label: 'Inappropriate or harmful', priority: 2 },
  { id: 'other', label: 'Other', priority: 3 },
]

function setup(extra: Record<string, unknown> = {}, me = makeMe()) {
  return mockApi({
    ...signedIn(me),
    'GET /users/ravi': { body: RAVI },
    'GET /media/options': {
      body: makeMediaOptions({ avatar_report_reasons: SERVER_REASONS }),
    },
    'POST /media/avatars/urls': { body: { items: {} } },
    'GET /notifications/preferences': {
      body: { categories: [], mutes: { people: [], deeds: [] } },
    },
    'GET /users/ravi/avatar-report-target': { body: { photo_id: 'ph-ravi' } },
    'POST /media/photos/ph-ravi/reports': { status: 202, body: { status: 'received' } },
    ...(extra as Record<string, never>),
  })
}

async function openReport() {
  const user = userEvent.setup()
  renderApp('/u/ravi')
  await user.click(
    await screen.findByRole('button', { name: 'More options for Ravi Kumar' }, { timeout: 4000 }),
  )
  await user.click(screen.getByRole('button', { name: 'Report profile picture' }))
  return { user, sheet: screen.getByRole('dialog', { name: 'Report profile picture' }) }
}

describe('who can report a profile picture', () => {
  it('not on my own profile', async () => {
    setup({ 'GET /users/ravi': { body: RAVI } }, makeMe({ handle: 'Ravi' }))
    renderApp('/u/ravi')
    await screen.findByRole('heading', { name: 'Ravi Kumar' }, { timeout: 4000 })
    expect(screen.queryByRole('button', { name: /More options/ })).toBeNull()
  })

  it('not when the person has no picture (the menu still offers mute)', async () => {
    setup({ 'GET /users/ravi': { body: { ...RAVI, avatar: null } } })
    const user = userEvent.setup()
    renderApp('/u/ravi')
    await user.click(
      await screen.findByRole('button', { name: 'More options for Ravi Kumar' }, { timeout: 4000 }),
    )
    expect(screen.queryByRole('button', { name: 'Report profile picture' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Mute notifications from Ravi' })).toBeInTheDocument()
  })

  it('not before onboarding is complete, even with a picture', async () => {
    setup({}, makeMe({ onboarding: NOT_ONBOARDED }))
    const user = userEvent.setup()
    renderApp('/u/ravi')
    await user.click(
      await screen.findByRole('button', { name: 'More options for Ravi Kumar' }, { timeout: 4000 }),
    )
    expect(screen.queryByRole('button', { name: 'Report profile picture' })).toBeNull()
  })
})

describe('the report sheet', () => {
  it('lists the server’s reasons in order, child first, with impersonation', async () => {
    setup()
    const { sheet } = await openReport()
    const names = within(sheet)
      .getAllByRole('radio')
      .map((r) => r.closest('label')?.textContent)
    expect(names).toEqual(SERVER_REASONS.map((r) => r.label))
    expect(names[0]).toBe('Shows a child')
  })

  it('the built-in fallback has the same reasons and order', () => {
    expect(AVATAR_REPORT_REASONS.map((r) => r.label)).toEqual(SERVER_REASONS.map((r) => r.label))
  })

  it('Send needs a reason; details are trimmed and sent', async () => {
    const api = setup()
    const { user, sheet } = await openReport()
    const send = within(sheet).getByRole('button', { name: 'Send report' })
    expect(send).toBeDisabled()
    await user.click(within(sheet).getByRole('radio', { name: 'Shows a child' }))
    await user.type(within(sheet).getByRole('textbox'), '   It is my niece  ')
    await user.click(send)
    expect(await screen.findByText(/Reports about children are looked at first/)).toBeVisible()
    expect(api.calls('POST /media/photos/ph-ravi/reports')[0].body).toEqual({
      reason: 'child_no_guardian_consent',
      details: 'It is my niece',
    })
  })

  it('whitespace-only details are left out', async () => {
    const api = setup()
    const { user, sheet } = await openReport()
    await user.click(within(sheet).getByRole('radio', { name: 'Other' }))
    await user.type(within(sheet).getByRole('textbox'), '    ')
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    await waitFor(() => expect(api.calls('POST /media/photos/ph-ravi/reports')).toHaveLength(1))
    expect(api.calls('POST /media/photos/ph-ravi/reports')[0].body).toEqual({ reason: 'other' })
  })

  it('the picture changed or was removed meanwhile (404)', async () => {
    const api = setup({
      'GET /users/ravi/avatar-report-target': {
        status: 404,
        body: { detail: 'Not found', code: 'avatar_not_found' },
      },
    })
    const { user, sheet } = await openReport()
    await user.click(within(sheet).getByRole('radio', { name: 'Private details' }))
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(
      'This profile picture isn’t available any more.',
    )
    expect(api.calls('POST /media/photos/ph-ravi/reports')).toHaveLength(0)
  })

  it('too many reports (429)', async () => {
    setup({
      'POST /media/photos/ph-ravi/reports': {
        status: 429,
        body: { detail: 'Slow down', code: 'rate_limited' },
      },
    })
    const { user, sheet } = await openReport()
    await user.click(within(sheet).getByRole('radio', { name: 'Inappropriate or harmful' }))
    await user.click(within(sheet).getByRole('button', { name: 'Send report' }))
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(
      'You’ve sent a lot of reports. Please try again later.',
    )
  })

  it('never tells who reported, and says the picture stays up meanwhile', async () => {
    setup()
    const { sheet } = await openReport()
    expect(sheet).toHaveTextContent('The picture stays up until our team decides.')
    expect(sheet).toHaveTextContent('Ravi won’t see who reported it.')
  })
})
