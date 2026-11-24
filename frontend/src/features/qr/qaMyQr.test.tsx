/**
 * QA4: My QR (F2 canvas, M2-DESIGN §5.3 F2). Reconnecting recovery, the large view while
 * reconnecting or expired, focus handling, and the uncopyable code.
 */

import { act, createEvent, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeMe, makeQr, mockApi, renderApp, signedIn } from '../../test/utils'

const ME = makeMe({ display_name: 'Ahmed Khan' })
const soon = (ms: number) => new Date(Date.now() + ms).toISOString()

describe('My QR: reconnecting', () => {
  it('leaves the reconnecting state when a later refresh succeeds', async () => {
    // The refresh schedule runs on a controlled clock, so a slow machine can't miss a step.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const api = mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1, expires_at: soon(60_000) }) },
        { status: 503, body: { detail: 'Unavailable' } },
        { body: makeQr({ refresh_after_seconds: 30, manual_code: '615207' }) },
      ],
    })
    renderApp('/me/qr')
    await screen.findByText('482 913')
    // The refresh due after 1 s fails...
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(await screen.findByText('Reconnecting…')).toBeVisible()
    expect(api.calls('GET /users/me/qr')).toHaveLength(2)
    // ...and the retry on the normal schedule shows the fresh code, not greyed, with the bar.
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(await screen.findByText('615 207')).toBeVisible()
    expect(screen.queryByText('Reconnecting…')).not.toBeInTheDocument()
    expect(screen.getByTestId('qr-frame')).not.toHaveClass('grayscale')
    expect(screen.getByRole('progressbar', { name: 'Time until the code changes' })).toBeVisible()
    expect(api.calls('GET /users/me/qr')).toHaveLength(3)
  })

  it('never shows the countdown during reconnecting, only the reconnecting note', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1, expires_at: soon(60_000) }) },
        { status: 503, body: { detail: 'Unavailable' } },
      ],
    })
    renderApp('/me/qr')
    await screen.findByText('Reconnecting…', {}, { timeout: 3000 })
    expect(screen.queryByText(/^New code in/)).not.toBeInTheDocument()
    // The Copy ID button keeps working (the ID never changes).
    expect(screen.getByRole('button', { name: 'Copy ID' })).toBeEnabled()
  })

  // BUG (Low): the large view ignores the reconnecting state. Once a refresh has failed it
  // keeps showing "New code in 0 s" (orange) at full strength, as if a new code were coming,
  // instead of the "Reconnecting…" note the page shows (§5.3 F2 Reconnecting: "in place of
  // the countdown").
  it('shows the reconnecting state inside the large view too', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1, expires_at: soon(60_000) }) },
        { status: 503, body: { detail: 'Unavailable' } },
      ],
    })
    const user = userEvent.setup()
    renderApp('/me/qr')
    await user.click(await screen.findByRole('button', { name: 'Show large QR' }))
    await screen.findByText('Reconnecting…', {}, { timeout: 3000 })
    const dialog = screen.getByRole('dialog', { name: 'Large QR code' })
    expect(within(dialog).queryByText(/^New code in/)).not.toBeInTheDocument()
    expect(within(dialog).getByText(/Reconnecting/)).toBeVisible()
  })

  it('closes the large view and shows the error once the last code really expires', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1, expires_at: soon(1800) }) },
        { status: 503, body: { detail: 'Unavailable' } },
      ],
    })
    const user = userEvent.setup()
    renderApp('/me/qr')
    await user.click(await screen.findByRole('button', { name: 'Show large QR' }))
    expect(screen.getByRole('dialog', { name: 'Large QR code' })).toBeVisible()
    expect(
      await screen.findByText('We couldn’t load your QR code.', {}, { timeout: 4000 }),
    ).toBeVisible()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('482 913')).not.toBeInTheDocument()
  })
})

describe('My QR: large view and code', () => {
  it('keeps focus on Close inside the large view (Tab and Shift+Tab)', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    const user = userEvent.setup()
    renderApp('/me/qr')
    await user.click(await screen.findByRole('button', { name: 'Show large QR' }))
    const close = within(screen.getByRole('dialog')).getByRole('button', { name: /Close/ })
    expect(close).toHaveFocus()
    await user.tab()
    expect(close).toHaveFocus()
    await user.tab({ shift: true })
    expect(close).toHaveFocus()
  })

  it('shows the ID and code but no copy control in the large view', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    const user = userEvent.setup()
    renderApp('/me/qr')
    await user.click(await screen.findByRole('button', { name: 'Show large QR' }))
    const dialog = screen.getByRole('dialog', { name: 'Large QR code' })
    expect(within(dialog).getAllByRole('button')).toHaveLength(1)
    expect(within(dialog).getByText('482 913')).toHaveClass('select-none')
  })

  it('blocks copying the code on the page, while it stays readable', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    renderApp('/me/qr')
    const code = await screen.findByText('482 913')
    const copy = createEvent.copy(code)
    fireEvent(code, copy)
    expect(copy.defaultPrevented).toBe(true)
    // Not hidden from assistive technology.
    expect(code).not.toHaveAttribute('aria-hidden')
    expect(code.closest('[aria-hidden="true"]')).toBeNull()
  })

  it('shows only the current code (no "previous code" line)', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    renderApp('/me/qr')
    await screen.findByText('482 913')
    expect(screen.queryByText(/previous code/i)).not.toBeInTheDocument()
    expect(screen.getAllByText(/^\d{3} \d{3}$/)).toHaveLength(1)
  })

  it('gives the page controls 44 px targets', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    renderApp('/me/qr')
    await screen.findByText('482 913')
    for (const name of ['Show large QR', 'Copy ID', 'Reset my QR']) {
      expect(screen.getByRole('button', { name }).className).toMatch(/\bmin-h-1[1-3]\b/)
    }
  })
})

describe('Q21 notice', () => {
  it('appears on My QR under the QR with the PO wording', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    renderApp('/me/qr')
    const qrCard = await screen.findByRole('region', { name: 'Your QR code' })
    await waitFor(() =>
      expect(qrCard.parentElement).toHaveTextContent(
        'When someone records a deed you did, it appears in the public Good Deeds feed with your name and the area. You can hide any deed from your profile at any time.',
      ),
    )
  })

  it('appears word for word in the Privacy Notice, also when signed out', async () => {
    mockApi()
    renderApp('/privacy')
    expect(
      await screen.findByText(
        'When someone records a deed you did, it appears in the public Good Deeds feed with your name and the area. You can hide any deed from your profile at any time.',
      ),
    ).toBeVisible()
  })
})
