/**
 * F16 loose ends (docs/M3-DESIGN.md §6.3): the unread-outcome dot on My deeds cards (local
 * storage), the "Shown in feed / Hidden from feed" chip on the owner view, "Verified just now ·
 * {area}" on the deed form, and the draft photo guidelines page.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getPosition, permissionState } from '../../lib/geolocation'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makeDetail } from '../feed/testFixtures'
import { GOOD_FIX, makeDeed, makeOptions, makeScanDetail, SCAN_ID } from '../report/testFixtures'
import type { DeedPhotosOut, Outcome } from './mediaApi'
import { hasUnreadOutcome, markOutcomesSeen } from './outcomeSeen'
import { makeMediaOptions, makeOwnerPhoto } from './testFixtures'

vi.mock('../../lib/geolocation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/geolocation')>()
  return { ...actual, getPosition: vi.fn(), permissionState: vi.fn() }
})

beforeEach(() => {
  vi.mocked(getPosition).mockReset().mockResolvedValue(GOOD_FIX)
  vi.mocked(permissionState).mockReset().mockResolvedValue('granted')
})

const ASHA = { handle: 'asha', display_name: 'Asha' }
const KEPT: Outcome = {
  decision: 'kept',
  removal_reason: null,
  removed_by: null,
  decided_at: '2026-11-18T10:00:00Z',
  photo_count: 1,
}

function photos(outcomes: Outcome[]): DeedPhotosOut {
  return { photos: [makeOwnerPhoto()], can_add: false, add_until: null, outcomes }
}

describe('unread outcome dot', () => {
  it('compares the newest outcome with what this device has seen', () => {
    expect(hasUnreadOutcome('d1', [])).toBe(false)
    expect(hasUnreadOutcome('d1', [KEPT])).toBe(true)
    markOutcomesSeen('d1', [KEPT])
    expect(hasUnreadOutcome('d1', [KEPT])).toBe(false)
    expect(hasUnreadOutcome('d1', [{ ...KEPT, decided_at: '2026-11-19T10:00:00Z' }])).toBe(true)
  })

  it('never throws when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => markOutcomesSeen('d1', [KEPT])).not.toThrow()
    expect(hasUnreadOutcome('d1', [KEPT])).toBe(true)
  })

  it('shows on the reported card until the deed is opened', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /deeds/options': { body: makeOptions() },
      'GET /media/options': { body: makeMediaOptions() },
      'GET /me/deeds': { body: { items: [makeDeed({ reporter: ASHA })], next_cursor: null } },
      'GET /deeds/deed-1': { body: makeDeed({ reporter: ASHA }) },
      'GET /media/deeds/deed-1/photos': { body: photos([KEPT]) },
      'GET /feed/deeds/deed-1': { body: makeDetail() },
    })
    const user = userEvent.setup()
    renderApp('/me/deeds?tab=reported')
    const card = await screen.findByRole('article')
    const link = within(within(card).getByRole('heading')).getByRole('link')
    await waitFor(() => expect(link).toHaveTextContent('(new photo review update)'))
    await user.click(link)
    expect(await screen.findByText('Review complete: your photos stay')).toBeInTheDocument()
    await waitFor(() => expect(hasUnreadOutcome('deed-1', [KEPT])).toBe(false))
  })
})

describe('feed visibility chip on My deeds detail', () => {
  function setup(listed: boolean) {
    mockApi({
      ...signedIn(makeMe()),
      'GET /deeds/options': { body: makeOptions() },
      'GET /deeds/deed-1': { body: makeDeed({ reporter: ASHA }) },
      'GET /feed/deeds/deed-1': {
        body: makeDetail({
          visibility: {
            listed,
            hidden_by_me: !listed,
            hidden_by_other: false,
            can_hide: listed,
            can_show: !listed,
          },
        }),
      },
    })
  }

  it.each([
    [true, 'Shown in feed'],
    [false, 'Hidden from feed'],
  ])('listed=%s shows "%s"', async (listed, label) => {
    setup(listed)
    renderApp('/me/deeds/deed-1')
    expect(await screen.findByText(label, undefined, { timeout: 3000 })).toBeInTheDocument()
  })

  it('shows no chip for a pending deed (it is not in the feed)', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      'GET /deeds/options': { body: makeOptions() },
      'GET /deeds/deed-1': { body: makeDeed({ reporter: ASHA, status: 'pending', doer: null }) },
    })
    renderApp('/me/deeds/deed-1')
    await screen.findByText('Helped an elderly man cross the road.')
    expect(screen.queryByText(/in feed|from feed/)).toBeNull()
    expect(api.calls('GET /feed/deeds/deed-1')).toHaveLength(0)
  })
})

describe('deed form area line', () => {
  it('shows "Verified just now · {area}" from the scan', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /deeds/options': { body: makeOptions() },
      [`GET /scans/${SCAN_ID}`]: { body: makeScanDetail({ area_name: 'Indiranagar' }) },
    })
    renderApp(`/report/scan/${SCAN_ID}`)
    expect(await screen.findByText('Verified just now · Indiranagar')).toBeInTheDocument()
  })
})

describe('/photo-guidelines', () => {
  it('is marked as a draft for the PO', async () => {
    mockApi()
    renderApp('/photo-guidelines')
    expect(await screen.findByRole('heading', { name: 'Photo guidelines' })).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('Draft — PO to approve')
    expect(screen.getByText(/Children need a parent’s consent/)).toBeInTheDocument()
  })
})
