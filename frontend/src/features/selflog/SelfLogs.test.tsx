/** F6b: self-logs list, delete, and the Log a deed form (create and edit). */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GeolocationError, getPosition } from '../../lib/geolocation'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { SelfLogOut } from '../deeds/deedsApi'
import { GOOD_FIX, makeOptions } from '../report/testFixtures'
import { fromInputs, toDateInput, toTimeInput, validateSelfLog } from './selfLogValues'

vi.mock('../../lib/geolocation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/geolocation')>()
  return { ...actual, getPosition: vi.fn() }
})
const getPositionMock = vi.mocked(getPosition)
beforeEach(() => {
  getPositionMock.mockReset()
  getPositionMock.mockResolvedValue(GOOD_FIX)
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
    'GET /users/asha/deed-counts': { body: { verified: 0 } },
    ...handlers,
  })
}

describe('self-logs list', () => {
  it('shows the private note, Log a deed and each log with Edit and Delete', async () => {
    setup([makeLog(), makeLog({ id: 'log-2', has_location: true, category: 'other' })])
    renderApp('/me/self-logs')

    expect(await screen.findByRole('heading', { level: 1, name: 'Self-logs' })).toBeVisible()
    expect(
      screen.getByText(
        'Self-logs are private and don’t earn points. If someone saw you, ask them to record it by scanning your QR.',
      ),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Log a deed' })).toHaveAttribute(
      'href',
      '/me/self-logs/new',
    )
    const items = await screen.findAllByRole('article')
    expect(items).toHaveLength(2)
    const first = items[0]
    expect(within(first).getByText('Only you')).toBeVisible()
    expect(within(first).getByText('14 Nov')).toBeVisible()
    expect(within(first).getByText('Animals')).toBeVisible()
    expect(within(first).getByText('Fed the stray dogs on my street before work.')).toBeVisible()
    expect(within(first).queryByText('Location added')).not.toBeInTheDocument()
    expect(within(items[1]).getByText('Location added')).toBeVisible()
    expect(within(first).getByRole('link', { name: /^Edit/ })).toHaveAttribute(
      'href',
      '/me/self-logs/log-1/edit',
    )
    expect(within(first).getByRole('button', { name: /^Delete/ })).toBeVisible()
    expect(screen.queryByText(/verify/i)).not.toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    setup([])
    renderApp('/me/self-logs')
    expect(await screen.findByRole('heading', { name: 'No self-logs yet' })).toBeVisible()
    expect(
      screen.getByText(
        'Keep a private note of the good things you do, even when no one is watching.',
      ),
    ).toBeVisible()
  })

  it('deletes only after confirming', async () => {
    const api = setup([makeLog()], { 'DELETE /me/self-logs/log-1': { status: 204 } })
    const user = userEvent.setup()
    renderApp('/me/self-logs')

    await user.click(await screen.findByRole('button', { name: /^Delete/ }))
    const dialog = screen.getByRole('dialog', { name: 'Delete this self-log?' })
    expect(dialog).toHaveAccessibleDescription(
      'It’s removed for good. Self-logs don’t affect your points.',
    )
    expect(within(dialog).getByRole('button', { name: 'Keep it' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.calls('DELETE /me/self-logs/log-1')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: /^Delete/ }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.calls('DELETE /me/self-logs/log-1')).toHaveLength(1)
    await waitFor(() => expect(api.calls('GET /me/self-logs').length).toBeGreaterThan(1))
  })

  it('shows an error with Try again', async () => {
    setup([], { 'GET /me/self-logs': { status: 500, body: { detail: 'Server trouble.' } } })
    renderApp('/me/self-logs')
    expect(await screen.findByRole('alert')).toHaveTextContent('Server trouble.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
  })
})

describe('Log a deed form', () => {
  it('creates a private self-log without location', async () => {
    const api = setup([], { 'POST /me/self-logs': { status: 201, body: makeLog() } })
    const user = userEvent.setup()
    const { router } = renderApp('/me/self-logs/new')

    expect(await screen.findByRole('heading', { name: 'Log a deed' })).toBeVisible()
    expect(
      screen.getByText('Private. Only you can see it, and it doesn’t earn points.'),
    ).toBeVisible()
    expect(screen.getByText('Any date in the past.')).toBeVisible()
    expect(screen.queryByText(/photo/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/verify/i)).not.toBeInTheDocument()

    const date = screen.getByLabelText('Date')
    expect(date).toHaveAttribute('max', toDateInput(new Date()))
    await user.click(screen.getByRole('button', { name: 'Animals' }))
    await user.type(screen.getByRole('textbox', { name: 'What did you do?' }), 'Fed the stray dogs')
    await user.clear(date)
    await user.type(date, '2026-11-10')
    const time = screen.getByLabelText('Time')
    await user.clear(time)
    await user.type(time, '07:15')
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs'))
    expect(api.calls('POST /me/self-logs')[0].body).toEqual({
      category: 'animals',
      description: 'Fed the stray dogs',
      occurred_at: new Date(2026, 10, 10, 7, 15).toISOString(),
    })
  })

  it('adds the current location when switched on', async () => {
    const api = setup([], { 'POST /me/self-logs': { status: 201, body: makeLog() } })
    const user = userEvent.setup()
    renderApp('/me/self-logs/new')
    await user.click(await screen.findByRole('button', { name: 'Animals' }))
    await user.type(screen.getByRole('textbox', { name: 'What did you do?' }), 'Fed the dogs')
    const toggle = screen.getByRole('switch', { name: /Add my current location/ })
    await user.click(toggle)
    await waitFor(() => expect(toggle).toBeChecked())
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    await waitFor(() => expect(api.calls('POST /me/self-logs')).toHaveLength(1))
    expect(api.calls('POST /me/self-logs')[0].body).toMatchObject({ location: GOOD_FIX })
  })

  it('turns the toggle off when location is blocked', async () => {
    getPositionMock.mockRejectedValue(new GeolocationError('denied', 'no'))
    setup([])
    const user = userEvent.setup()
    renderApp('/me/self-logs/new')
    const toggle = await screen.findByRole('switch', { name: /Add my current location/ })
    await user.click(toggle)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Location is blocked in your browser.',
    )
    expect(toggle).not.toBeChecked()
  })

  it('checks the fields before saving', async () => {
    const api = setup([])
    const user = userEvent.setup()
    renderApp('/me/self-logs/new')
    const date = await screen.findByLabelText('Date')
    await user.clear(date)
    await user.type(date, '2099-01-01')
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))
    expect(screen.getByText('Choose what kind of deed it was.')).toBeVisible()
    expect(
      screen.getByText('Write at least 10 characters so people understand what happened.'),
    ).toBeVisible()
    expect(screen.getByText('Choose a time that has already passed.')).toBeVisible()
    expect(api.calls('POST /me/self-logs')).toHaveLength(0)
  })

  it('edits a log: sends the changes and can remove its location', async () => {
    const log = makeLog({ has_location: true })
    const api = setup([log], {
      'PATCH /me/self-logs/log-1': { body: makeLog({ has_location: false }) },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/self-logs/log-1/edit')

    expect(await screen.findByRole('heading', { name: 'Edit self-log' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Animals' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('textbox', { name: 'What did you do?' })).toHaveValue(log.description)
    expect(screen.getByLabelText('Date')).toHaveValue(toDateInput(new Date(log.occurred_at)))
    expect(screen.getByText('Location added')).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(screen.getByRole('switch', { name: /Add my current location/ })).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Other' }))
    await user.click(screen.getByRole('button', { name: 'Save self-log' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/me/self-logs'))
    expect(api.calls('PATCH /me/self-logs/log-1')[0].body).toEqual({
      category: 'other',
      location: null,
    })
  })

  it('finds a log on a later page', async () => {
    let call = 0
    setup([], {
      'GET /me/self-logs': () => {
        call += 1
        return call === 1
          ? { body: { items: [makeLog({ id: 'other' })], next_cursor: 'c2' } }
          : {
              body: {
                items: [makeLog({ id: 'log-9', description: 'Second page log' })],
                next_cursor: null,
              },
            }
      },
    })
    renderApp('/me/self-logs/log-9/edit')
    expect(await screen.findByRole('textbox', { name: 'What did you do?' })).toHaveValue(
      'Second page log',
    )
  })

  it('says when the log no longer exists', async () => {
    setup([makeLog()])
    renderApp('/me/self-logs/gone/edit')
    expect(await screen.findByRole('alert')).toHaveTextContent('This self-log isn’t available.')
  })
})

describe('selfLogValues', () => {
  it('round-trips local date and time', () => {
    const when = new Date(2026, 10, 10, 7, 5)
    expect(toDateInput(when)).toBe('2026-11-10')
    expect(toTimeInput(when)).toBe('07:05')
    expect(fromInputs('2026-11-10', '07:05')?.getTime()).toBe(when.getTime())
    expect(fromInputs('', '07:05')).toBeNull()
  })

  it('needs a date and time', () => {
    expect(validateSelfLog({ category: 'a', description: 'x', date: '', time: '' }).when).toBe(
      'Choose a date and time.',
    )
  })
})
