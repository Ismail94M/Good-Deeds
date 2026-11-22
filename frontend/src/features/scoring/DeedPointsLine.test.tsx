/** F15: the own points line on the deed page (M4-DESIGN §6.3, §3.3 #5). */

import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { AHMED, makeDeed, makeOptions as makeDeedOptions } from '../report/testFixtures'

const PRIYA = { handle: 'priyas', display_name: 'Priya Sharma' }
const ME = makeMe({ handle: PRIYA.handle, display_name: PRIYA.display_name })

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /deeds/options': { body: makeDeedOptions() },
    'GET /deeds/d1': { body: makeDeed({ id: 'd1', reporter: AHMED, doer: PRIYA }) },
    ...handlers,
  })
}

const line = () => screen.findByRole('region', { name: 'Your points for this deed' })

describe('DeedPointsLine on /me/deeds/:id', () => {
  it('shows my points and until when they are provisional (doer)', async () => {
    setup({
      'GET /scoring/deeds/d1': {
        body: {
          points: 10,
          reason: 'verified',
          reason_label: 'Deed verified',
          capped: false,
          provisional_until: '2026-12-03T08:35:00Z',
        },
      },
    })
    renderApp('/me/deeds/d1')
    const section = await line()
    expect(section).toHaveTextContent('+10 points · provisional until 3 Dec, 14:05')
    expect(section).toHaveTextContent('Only you can see your points.')
  })

  it('shows the reporter’s line without a date once final', async () => {
    setup({
      'GET /deeds/r1': { body: makeDeed({ id: 'r1', reporter: PRIYA, doer: AHMED }) },
      'GET /scoring/deeds/r1': {
        body: {
          points: 2,
          reason: 'verified',
          reason_label: 'Deed verified',
          capped: false,
          provisional_until: null,
        },
      },
    })
    renderApp('/me/deeds/r1')
    const section = await line()
    expect(section).toHaveTextContent('+2 points')
    expect(section).not.toHaveTextContent('provisional')
  })

  it('explains a capped deed with the server’s reason text', async () => {
    setup({
      'GET /scoring/deeds/d1': {
        body: {
          points: 0,
          reason: 'doer_daily_cap',
          reason_label: 'Daily limit reached: still counts on your profile',
          capped: true,
          provisional_until: null,
        },
      },
    })
    renderApp('/me/deeds/d1')
    const section = await line()
    await waitFor(() =>
      expect(section).toHaveTextContent(
        '0 points · Daily limit reached: still counts on your profile',
      ),
    )
  })

  it('hides the line on a 404 and never asks for a pending deed', async () => {
    const api = setup({
      'GET /scoring/deeds/d1': { status: 404, body: { detail: 'Not found.', code: 'not_found' } },
      'GET /deeds/p1': {
        body: makeDeed({ id: 'p1', status: 'pending', reporter: PRIYA, doer: null }),
      },
    })
    const { unmount } = renderApp('/me/deeds/d1')
    await waitFor(() => expect(api.calls('GET /scoring/deeds/d1')).toHaveLength(1))
    await screen.findByText('Helped an elderly man cross the road.')
    expect(screen.queryByRole('region', { name: 'Your points for this deed' })).toBeNull()
    unmount()

    renderApp('/me/deeds/p1')
    await screen.findByText('Helped an elderly man cross the road.')
    expect(api.calls('GET /scoring/deeds/p1')).toHaveLength(0)
  })
})
