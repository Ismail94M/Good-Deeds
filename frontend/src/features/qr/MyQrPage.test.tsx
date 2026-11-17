import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeMe, makeQr, mockApi, renderApp, signedIn } from '../../test/utils'
import { PUBLIC_FEED_NOTICE } from './qrFormat'

const ME = makeMe({ display_name: 'Ahmed Khan' })
const soon = (ms: number) => new Date(Date.now() + ms).toISOString()

describe('MyQrPage', () => {
  it('shows the QR, the countdown bar, the ID with Copy ID and the uncopyable code', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    renderApp('/me/qr')

    expect(await screen.findByRole('heading', { name: 'My QR code' })).toBeVisible()
    expect(
      screen.getByText(
        'Show this to someone who saw your good deed. They scan it to recognise you.',
      ),
    ).toBeVisible()
    const qr = await screen.findByRole('img', { name: 'QR code for GD-7K3M9Q0' })
    await waitFor(() => expect(qr.querySelector('svg')).not.toBeNull())

    expect(screen.getByText(/^New code in (29|30) s$/)).toBeVisible()
    expect(screen.getByText('Changes every 30 s')).toBeVisible()
    const bar = screen.getByRole('progressbar', { name: 'Time until the code changes' })
    expect(bar).toHaveAttribute('aria-valuemax', '30')

    const idCard = screen.getByRole('region', { name: 'No camera? Give them your ID and code' })
    expect(within(idCard).getByText('GD-7K3M9Q0')).toBeVisible()
    expect(within(idCard).getByText('482 913')).toHaveClass('select-none')
    // Only the ID can be copied.
    expect(within(idCard).getAllByRole('button')).toHaveLength(1)
    expect(within(idCard).getByRole('button', { name: 'Copy ID' })).toBeVisible()
    expect(
      within(idCard).getByText(
        'They type both. The code changes every 30 s and can’t be copied, so read it out or let them look at your screen.',
      ),
    ).toBeVisible()
  })

  it('names Record a deed and tells the doer deeds about them are public (Q21)', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    renderApp('/me/qr')
    const how = await screen.findByRole('region', {
      name: 'How the person who saw your deed records it',
    })
    expect(how).toHaveTextContent('They open Record a deed in Good Deeds and scan your QR.')
    expect(how).toHaveTextContent(
      'No camera, or it won’t scan? They tap Enter ID and code and type the two above.',
    )
    expect(within(how).getByText(PUBLIC_FEED_NOTICE)).toBeVisible()
    // Also directly under the QR.
    expect(screen.getAllByText(PUBLIC_FEED_NOTICE)).toHaveLength(2)
  })

  it('follows window_seconds from config, falling back to 30', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': { body: makeQr({ window_seconds: 20, refresh_after_seconds: 12 }) },
    })
    renderApp('/me/qr')
    expect(await screen.findByText('Changes every 20 s')).toBeVisible()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '20')
  })

  it('falls back to 30 s when window_seconds is missing', async () => {
    const qr = makeQr() as Partial<ReturnType<typeof makeQr>>
    delete qr.window_seconds
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: qr } })
    renderApp('/me/qr')
    expect(await screen.findByText('Changes every 30 s')).toBeVisible()
  })

  it('is blue before the last 5 seconds', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': { body: makeQr({ refresh_after_seconds: 25 }) },
    })
    renderApp('/me/qr')
    const label = await screen.findByText(/^New code in \d+ s$/)
    expect(label).toHaveClass('text-brand-700')
    expect(label).not.toHaveAttribute('data-ending')
  })

  it('is orange at 5 s or less', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr({ refresh_after_seconds: 4 }) } })
    renderApp('/me/qr')
    const label = await screen.findByText(/^New code in [1-4] s$/)
    expect(label).toHaveClass('text-orange-700')
    expect(label).toHaveAttribute('data-ending', 'true')
  })

  it('fetches a new code when the server says to', async () => {
    const api = mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1 }) },
        { body: makeQr({ refresh_after_seconds: 30, manual_code: '615207' }) },
      ],
    })
    renderApp('/me/qr')
    await screen.findByText('482 913')
    expect(await screen.findByText('615 207', {}, { timeout: 3000 })).toBeVisible()
    expect(api.calls('GET /users/me/qr')).toHaveLength(2)
  })

  it('copies the ID and shows "Copied" and a toast for a moment', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    renderApp('/me/qr')

    await user.click(await screen.findByRole('button', { name: 'Copy ID' }))
    expect(writeText).toHaveBeenCalledWith('GD-7K3M9Q0')
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('ID copied')
    expect(await screen.findByRole('button', { name: 'Copy ID' }, { timeout: 4000 })).toBeVisible()
    expect(screen.queryByText('ID copied')).not.toBeInTheDocument()
  })

  it('opens the large QR full screen and closes it with Close or Esc', async () => {
    mockApi({ ...signedIn(ME), 'GET /users/me/qr': { body: makeQr() } })
    const user = userEvent.setup()
    renderApp('/me/qr')

    const open = await screen.findByRole('button', { name: 'Show large QR' })
    await user.click(open)
    const dialog = screen.getByRole('dialog', { name: 'Large QR code' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByText('Ahmed Khan · My QR')).toBeVisible()
    expect(within(dialog).getByRole('img', { name: 'QR code for GD-7K3M9Q0' })).toBeVisible()
    expect(within(dialog).getByText('GD-7K3M9Q0')).toBeVisible()
    expect(within(dialog).getByText('482 913')).toBeVisible()
    expect(within(dialog).getByText(/^New code in \d+ s$/)).toBeVisible()
    const close = within(dialog).getByRole('button', { name: /Close/ })
    expect(close).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(open).toHaveFocus()

    await user.click(open)
    await user.click(screen.getByRole('button', { name: /Close/ }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps the last code, greyed, while reconnecting', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1, expires_at: soon(60_000) }) },
        { status: 503, body: { detail: 'Unavailable' } },
      ],
    })
    renderApp('/me/qr')
    await screen.findByText('482 913')

    const status = await screen.findByText('Reconnecting…', {}, { timeout: 3000 })
    expect(status.closest('[role="status"]')).toHaveTextContent(
      'Reconnecting…You can keep showing this code for now.',
    )
    expect(screen.getByText('482 913')).toBeVisible()
    expect(screen.getByText('GD-7K3M9Q0')).toBeVisible()
    expect(screen.getByTestId('qr-frame')).toHaveClass('grayscale')
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText("We couldn't load your QR code.")).not.toBeInTheDocument()
  })

  it('shows the error state once the last code has really expired', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [
        { body: makeQr({ refresh_after_seconds: 1, expires_at: soon(1800) }) },
        { status: 503, body: { detail: 'Unavailable' } },
      ],
    })
    renderApp('/me/qr')
    await screen.findByText('482 913')

    expect(
      await screen.findByText('We couldn’t load your QR code.', {}, { timeout: 4000 }),
    ).toBeVisible()
    expect(screen.getByText('QR code unavailable')).toBeVisible()
    expect(screen.queryByText('482 913')).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /QR code for/ })).not.toBeInTheDocument()
  })

  it('shows the error state when the first load fails, and tries again', async () => {
    const api = mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': [{ status: 500, body: { detail: 'boom' } }, { body: makeQr() }],
    })
    const user = userEvent.setup()
    renderApp('/me/qr')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'We couldn’t load your QR code.Check your internet connection and try again.',
    )
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('482 913')).toBeVisible()
    expect(api.calls('GET /users/me/qr')).toHaveLength(2)
  })

  it('shows a loading state first', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': () => new Promise(() => undefined),
    })
    renderApp('/me/qr')
    expect(await screen.findByText('Loading your QR code…')).toBeVisible()
  })

  it('explains when onboarding is not finished', async () => {
    mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': {
        status: 403,
        body: { detail: 'Finish setting up.', code: 'onboarding_required' },
      },
    })
    renderApp('/me/qr')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Finish setting up to get your QR code.People can record your deeds once you’ve confirmed you’re 18 or over, accepted the terms and verified your phone number.',
    )
    expect(screen.getByRole('link', { name: 'Finish setting up' })).toHaveAttribute(
      'href',
      '/onboarding',
    )
  })

  it('resets the QR only after confirming', async () => {
    const api = mockApi({
      ...signedIn(ME),
      'GET /users/me/qr': { body: makeQr() },
      'POST /users/me/qr/rotate': {
        body: makeQr({ scan_url: 'http://localhost:5173/scan/new', token: 'new' }),
      },
    })
    const user = userEvent.setup()
    renderApp('/me/qr')

    expect(
      await screen.findByText('Resetting makes old screenshots of your QR stop working.'),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Reset my QR' }))
    expect(
      screen.getByText(
        'Resetting makes every earlier copy of your QR code stop working. Your ID stays the same.',
      ),
    ).toBeVisible()
    expect(api.calls('POST /users/me/qr/rotate')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Reset my QR' }))
    await user.click(screen.getByRole('button', { name: 'Yes, reset my QR' }))

    expect(
      await screen.findByText('Your QR code has been reset. Old screenshots no longer work.'),
    ).toBeVisible()
    expect(api.calls('POST /users/me/qr/rotate')).toHaveLength(1)
  })
})

describe('Privacy Notice', () => {
  it('tells people that deeds about them appear in the public feed (Q21)', async () => {
    mockApi()
    renderApp('/privacy')
    expect(await screen.findByText(PUBLIC_FEED_NOTICE)).toBeVisible()
  })
})
