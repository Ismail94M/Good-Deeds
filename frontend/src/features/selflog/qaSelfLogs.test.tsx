/**
 * QA4: self-logs (F6 canvas, M2-DESIGN §5.3, D3). No future dates, the delete dialog, edits
 * that send only what changed, and no link to verification.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getPosition } from '../../lib/geolocation'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { SelfLogOut } from '../deeds/deedsApi'
import { GOOD_FIX, makeOptions } from '../report/testFixtures'
import { toDateInput, toTimeInput } from './selfLogValues'

vi.mock('../../lib/geolocation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/geolocation')>()
  return { ...actual, getPosition: vi.fn() }
})
beforeEach(() => {
  vi.mocked(getPosition).mockReset().mockResolvedValue(GOOD_FIX)
})

function makeLog(overrides: Partial<SelfLogOut> = {}): SelfLogOut {
  return {
    id: 'log-1',
    category: 'animals',
    description: 'Fed the stray dogs on my street before work.',
    occurred_at: '2026-11-14T07:15:00+05:30',
    has_location: false,
    created_at: '2026-11-14T08:00:00+05:30',
    updated_at: '2026-11-14T08:00:00+05:30',
    ...overrides,
  }
}

function setup(logs: SelfLogOut[], handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(makeMe()),
    'GET /deeds/options': { body: makeOptions() },
    'GET /me/self-logs': { body: { items: logs, next_cursor: null } },
    'POST /me/self-logs': { status: 201, body: makeLog() },
    ...handlers,
  })
}

async function fillCreate(user: ReturnType<typeof userEvent.setup>, description: string) {
  await user.click(await screen.findByRole('button', { name: 'Animals' }))
  await user.type(screen.getByRole('textbox', { name: 'What did you do?' }), description)
}

describe('Log a deed: no future dates', () => {
  it('caps the date input at today', async () => {
    setup([])
    renderApp('/me/self-logs/new')
    expect(await screen.findByLabelText('Date')).toHaveAttribute('max', toDateInput(new Date()))
    expect(screen.getByText('Any date in the past.')).toBeVisible()
  })

  it('refuses a later time today (or any future moment) before calling the API', async () => {
    const api = setup([])
    const user = userEvent.setup()
    renderApp('/me/self-logs/new')
    await fillCreate(user, 'Fed the stray dogs on my street.')
    const future = new Date(Date.now() + 2 * 60 * 60 * 1000)
    const date = screen.getByLabelText('Date')
    const time = screen.getByLabelText('Time')
    await user.clear(date)
    await user.type(date, toDateInput(future))
    await user.clear(time)
    await user.type(time, toTimeInput(future))
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    expect(screen.getByText('Choose a time that has already passed.')).toBeVisible()
    expect(date).toHaveAttribute('aria-invalid', 'true')
    expect(api.calls('POST /me/self-logs')).toHaveLength(0)
  })

  it('accepts a date in the past and sends it as an ISO time', async () => {
    const api = setup([])
    const user = userEvent.setup()
    const { router } = renderApp('/me/self-logs/new')
    await fillCreate(user, 'Fed the stray dogs on my street.')
    const date = screen.getByLabelText('Date')
    const time = screen.getByLabelText('Time')
    await user.clear(date)
    await user.type(date, '2026-01-05')
    await user.clear(time)
    await user.type(time, '18:30')
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs'))
    expect(api.calls('POST /me/self-logs')[0].body).toEqual({
      category: 'animals',
      description: 'Fed the stray dogs on my street.',
      occurred_at: new Date(2026, 0, 5, 18, 30).toISOString(),
    })
  })
})

describe('Log a deed: description length', () => {
  // BUG (Low): the API rejects self-log descriptions shorter than `description_min` (10) with
  // 400 invalid_description (backend `_self_logs.py` uses the same check as deeds), but the form
  // only checks that the text isn't empty. "Helped" goes to the API and comes back as a
  // generic server message instead of an inline check like the deed and edit forms give.
  it('checks description_min on the client like the other forms', async () => {
    const api = setup([], {
      'POST /me/self-logs': {
        status: 400,
        body: {
          detail: 'Please describe the deed in 10 to 1000 characters.',
          code: 'invalid_description',
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/me/self-logs/new')
    await fillCreate(user, 'Helped')
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    expect(api.calls('POST /me/self-logs')).toHaveLength(0)
  })

  it('shows the API’s message when the server refuses a description', async () => {
    setup([], {
      'POST /me/self-logs': {
        status: 400,
        body: {
          detail: 'Please describe the deed in 10 to 1000 characters.',
          code: 'invalid_description',
        },
      },
    })
    const user = userEvent.setup()
    renderApp('/me/self-logs/new')
    // Long enough for the client check (fix for the test above), so the API's refusal shows.
    await fillCreate(user, 'Helped out')
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    expect(
      await screen.findByText('Please describe the deed in 10 to 1000 characters.'),
    ).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'What did you do?' })).toHaveValue('Helped out')
  })
})

describe('Log a deed: private, no verification (D3)', () => {
  it('has no verify button, no link to deeds or Record a deed, and no photo controls', async () => {
    setup([])
    renderApp('/me/self-logs/new')
    const form = (await screen.findByRole('button', { name: 'Save self-log' })).closest(
      'form',
    ) as HTMLFormElement
    expect(within(form).queryByText(/verif/i)).not.toBeInTheDocument()
    expect(within(form).queryAllByRole('link')).toHaveLength(0)
    expect(within(form).queryByText(/photo/i)).not.toBeInTheDocument()
    expect(
      screen.getByText('Private. Only you can see it, and it doesn’t earn points.'),
    ).toBeVisible()
  })
})

describe('self-log edit', () => {
  it('sends nothing when nothing changed', async () => {
    const api = setup([makeLog()])
    const user = userEvent.setup()
    const { router } = renderApp('/me/self-logs/log-1/edit')
    await user.click(await screen.findByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs'))
    expect(api.calls('PATCH /me/self-logs/log-1')).toHaveLength(0)
  })

  it('replaces a stored location with a new one', async () => {
    const api = setup([makeLog({ has_location: true })], {
      'PATCH /me/self-logs/log-1': { body: makeLog({ has_location: true }) },
    })
    const user = userEvent.setup()
    renderApp('/me/self-logs/log-1/edit')
    await user.click(await screen.findByRole('button', { name: 'Remove' }))
    await user.click(screen.getByRole('switch', { name: /Add my current location/ }))
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: /Add my current location/ })).toBeChecked(),
    )
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(api.calls('PATCH /me/self-logs/log-1')).toHaveLength(1))
    expect(api.calls('PATCH /me/self-logs/log-1')[0].body).toEqual({ location: GOOD_FIX })
  })

  it('refuses moving an existing log into the future', async () => {
    const api = setup([makeLog()])
    const user = userEvent.setup()
    renderApp('/me/self-logs/log-1/edit')
    const date = await screen.findByLabelText('Date')
    await user.clear(date)
    await user.type(date, '2099-01-01')
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    expect(screen.getByText('Choose a time that has already passed.')).toBeVisible()
    expect(api.calls('PATCH /me/self-logs/log-1')).toHaveLength(0)
  })
})

describe('delete dialog', () => {
  it('lists Delete then Keep it, traps Tab, and Esc returns focus to the opener', async () => {
    setup([makeLog()])
    const user = userEvent.setup()
    renderApp('/me/self-logs')
    const opener = await screen.findByRole('button', { name: /^Delete self-log from/ })
    await user.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Delete this self-log?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const buttons = within(dialog).getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['Delete', 'Keep it'])
    expect(buttons[1]).toHaveFocus()
    await user.tab()
    expect(buttons[0]).toHaveFocus()
    await user.tab()
    expect(buttons[1]).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('shows a failed delete inside the dialog and keeps the log', async () => {
    setup([makeLog()], {
      'DELETE /me/self-logs/log-1': { status: 500, body: { detail: 'Something broke.' } },
    })
    const user = userEvent.setup()
    renderApp('/me/self-logs')
    await user.click(await screen.findByRole('button', { name: /^Delete self-log from/ }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toBeVisible()
    expect(screen.getByText('Fed the stray dogs on my street before work.')).toBeVisible()
  })

  it('labels Edit and Delete per log and gives them 44 px', async () => {
    setup([makeLog()])
    renderApp('/me/self-logs')
    const edit = await screen.findByRole('link', { name: /^Edit self-log from/ })
    const del = screen.getByRole('button', { name: /^Delete self-log from/ })
    expect(edit).toHaveAttribute('href', '/me/self-logs/log-1/edit')
    expect(edit.className).toMatch(/\bmin-h-11\b/)
    expect(del.className).toMatch(/\bmin-h-11\b/)
  })
})
