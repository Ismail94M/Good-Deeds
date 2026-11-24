/**
 * F3a: finding the doer. Location gate, ID + code, `/scan/:token`, the in-app camera and the
 * camera-app fallback, Checking, and every scan-time error screen.
 */

import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GeolocationError, getPosition, permissionState } from '../../lib/geolocation'
import indexHtml from '../../../index.html?raw'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn, type Reply } from '../../test/utils'
import { AHMED, GOOD_FIX, makeOptions, makeScan, makeScanDetail, SCAN_ID } from './testFixtures'

vi.mock('../../lib/geolocation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/geolocation')>()
  return { ...actual, getPosition: vi.fn(), permissionState: vi.fn() }
})

const getPositionMock = vi.mocked(getPosition)
const permissionMock = vi.mocked(permissionState)

beforeEach(() => {
  getPositionMock.mockReset()
  permissionMock.mockReset()
  getPositionMock.mockResolvedValue(GOOD_FIX)
  permissionMock.mockResolvedValue('granted')
})

type Handler = NonNullable<Parameters<typeof mockApi>[0]>[string]

function api(scanReply: Handler = { status: 201, body: makeScan() }) {
  return mockApi({
    ...signedIn(makeMe()),
    'GET /deeds/options': { body: makeOptions() },
    'POST /scans': scanReply,
    [`GET /scans/${SCAN_ID}`]: { body: makeScanDetail() },
  })
}

async function fillManual(id = 'gd 7k3m 9q0', code = '482 913') {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Their ID'), id)
  await user.type(screen.getByLabelText('6-digit code'), code)
  await user.click(screen.getByRole('button', { name: 'Continue' }))
  return user
}

describe('LocationGate', () => {
  it('skips the explain screen when location is already allowed', async () => {
    api()
    renderApp('/report/code')
    expect(await screen.findByRole('heading', { name: 'Enter their ID and code' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Allow your location' })).not.toBeInTheDocument()
    expect(screen.getByText('Location ready · ±35 m')).toBeInTheDocument()
    expect(getPositionMock).toHaveBeenCalledTimes(1)
  })

  it('explains first, then asks the browser', async () => {
    permissionMock.mockResolvedValue('prompt')
    api()
    const user = userEvent.setup()
    renderApp('/report/code')

    expect(await screen.findByRole('heading', { name: 'Allow your location' })).toBeVisible()
    expect(screen.getByText('Your exact location is never shown to anyone.')).toBeVisible()
    expect(screen.getByText('It’s only used to check this report.')).toBeVisible()
    expect(screen.getByText('Good Deeds works in Bangalore for now.')).toBeVisible()
    expect(getPositionMock).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Allow location' }))
    expect(await screen.findByRole('heading', { name: 'Enter their ID and code' })).toBeVisible()
    expect(getPositionMock).toHaveBeenCalledTimes(1)
  })

  it('treats an unknown permission state like prompt', async () => {
    permissionMock.mockResolvedValue('unknown')
    api()
    renderApp('/report/scan')
    // No BarcodeDetector in jsdom: the camera-app screen needs no location at all.
    expect(await screen.findByRole('heading', { name: 'Scan with your camera app' })).toBeVisible()
    expect(permissionMock).not.toHaveBeenCalled()
  })

  it('shows the denied screen and re-requests on Try again', async () => {
    permissionMock.mockResolvedValue('prompt')
    getPositionMock
      .mockRejectedValueOnce(new GeolocationError('denied', 'no'))
      .mockResolvedValueOnce(GOOD_FIX)
    api()
    const user = userEvent.setup()
    renderApp('/report/code')

    await user.click(await screen.findByRole('button', { name: 'Allow location' }))
    expect(await screen.findByRole('heading', { name: 'Location is blocked' })).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent(
      "We can't verify a deed without your location. You can turn it back on in a few taps.",
    )
    expect(
      screen.getByText('Tap the icon to the left of the web address (a lock or settings icon).'),
    ).toBeVisible()
    expect(
      screen.getByText(
        'Still not working? Check that Location is switched on in your phone’s settings.',
      ),
    ).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Enter their ID and code' })).toBeVisible()
    expect(getPositionMock).toHaveBeenCalledTimes(2)
  })

  it('goes straight to the denied screen when the browser already blocks location', async () => {
    permissionMock.mockResolvedValue('denied')
    api()
    renderApp('/report/code')
    expect(await screen.findByRole('heading', { name: 'Location is blocked' })).toBeVisible()
    expect(getPositionMock).not.toHaveBeenCalled()
  })

  it('checks precision on the client before calling the API', async () => {
    getPositionMock
      .mockResolvedValueOnce({ ...GOOD_FIX, accuracy_m: 850 })
      .mockResolvedValueOnce(GOOD_FIX)
    const mock = api()
    const user = userEvent.setup()
    renderApp('/report/code')

    expect(
      await screen.findByRole('heading', { name: "Your location isn't precise enough" }),
    ).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Your phone places you somewhere within 850 m. We need it within 500 m.',
    )
    expect(screen.getByText('Step outside, or move near a window.')).toBeVisible()
    expect(mock.calls('POST /scans')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Enter their ID and code' })).toBeVisible()
  })

  it('uses the not-precise layout when the position times out', async () => {
    getPositionMock.mockRejectedValue(new GeolocationError('timeout', 'slow'))
    api()
    renderApp('/report/code')
    expect(
      await screen.findByRole('heading', { name: "We couldn't find your location" }),
    ).toBeVisible()
    expect(screen.getByText('Wait a few seconds, then try again.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
  })

  it('Back returns to the hub', async () => {
    permissionMock.mockResolvedValue('prompt')
    api()
    const user = userEvent.setup()
    const { router } = renderApp('/report/code')
    await user.click(await screen.findByRole('button', { name: 'Back' }))
    expect(router.state.location.pathname).toBe('/report')
  })

  it('keeps the position when moving between Record a deed pages', async () => {
    api()
    const user = userEvent.setup()
    const { router } = renderApp('/report')
    await user.click(await screen.findByRole('button', { name: /Enter ID and code/ }))
    await screen.findByRole('heading', { name: 'Enter their ID and code' })
    await user.click(screen.getByRole('button', { name: 'Back' }))
    await user.click(await screen.findByRole('button', { name: /Enter ID and code/ }))
    await screen.findByRole('heading', { name: 'Enter their ID and code' })
    expect(router.state.location.pathname).toBe('/report/code')
    expect(getPositionMock).toHaveBeenCalledTimes(1)
  })
})

describe('Enter ID and code', () => {
  it('normalises the ID and code, checks, and replaces the page with the deed form', async () => {
    let release: (reply: Reply) => void = () => undefined
    const mock = api(() => new Promise<Reply>((resolve) => (release = resolve)))
    const { router } = renderApp('/report/code')

    await fillManual()
    expect(await screen.findByRole('heading', { name: 'Checking…' })).toBeVisible()
    expect(
      screen.getByText('Matching the code and making sure you’re both in the same place.'),
    ).toBeVisible()
    act(() => release({ status: 201, body: makeScan() }))

    expect(await screen.findByText('Recognising')).toBeVisible()
    expect(router.state.location.pathname).toBe(`/report/scan/${SCAN_ID}`)
    expect(router.state.historyAction).toBe('REPLACE')
    expect(mock.calls('POST /scans')[0].body).toEqual({
      short_id: 'GD-7K3M9Q0',
      code: '482913',
      location: GOOD_FIX,
    })
  })

  it('shows "ID and code don\'t match" inline and keeps what was typed', async () => {
    api({ status: 404, body: { detail: 'No match.', code: 'doer_code_invalid' } })
    renderApp('/report/code')
    await fillManual('GD-7K3M9Q0', '482931')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("ID and code don't match.")
    expect(alert).toHaveTextContent(
      'Codes change every 30 seconds. Ask for the code on their screen right now and try again.',
    )
    const code = screen.getByLabelText('6-digit code')
    expect(code).toHaveAttribute('aria-invalid', 'true')
    expect(code).toHaveValue('482931')
    expect(screen.getByLabelText('Their ID')).toHaveValue('GD-7K3M9Q0')
    expect(screen.getByRole('button', { name: 'Continue' })).toBeVisible()
  })

  it('checks the fields before calling the API', async () => {
    const mock = api()
    renderApp('/report/code')
    await fillManual('GD-12', '12')
    expect(
      screen.getByText('Check their ID. It is GD- followed by 7 letters and numbers.'),
    ).toBeVisible()
    expect(screen.getByText('Enter the 6-digit code from their screen.')).toBeVisible()
    expect(screen.getByLabelText('Their ID')).toHaveFocus()
    expect(mock.calls('POST /scans')).toHaveLength(0)
  })

  it('takes a fresh position when the held one is over a minute old', async () => {
    const mock = api()
    renderApp('/report/code')
    await screen.findByLabelText('Their ID')
    const later = Date.now() + 61_000
    vi.spyOn(Date, 'now').mockReturnValue(later)
    getPositionMock.mockResolvedValueOnce({ ...GOOD_FIX, accuracy_m: 20 })

    await fillManual()
    await waitFor(() => expect(mock.calls('POST /scans')).toHaveLength(1))
    expect(getPositionMock).toHaveBeenCalledTimes(2)
    expect(mock.calls('POST /scans')[0].body).toMatchObject({ location: { accuracy_m: 20 } })
  })

  it('reuses a position under a minute old', async () => {
    const mock = api()
    renderApp('/report/code')
    await fillManual()
    await waitFor(() => expect(mock.calls('POST /scans')).toHaveLength(1))
    expect(getPositionMock).toHaveBeenCalledTimes(1)
  })

  it('returns to the not-precise screen on a 400 location_too_imprecise', async () => {
    api({ status: 400, body: { detail: 'Too imprecise.', code: 'location_too_imprecise' } })
    renderApp('/report/code')
    await fillManual()
    expect(
      await screen.findByRole('heading', { name: "Your location isn't precise enough" }),
    ).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent('within 35 m. We need it within 500 m.')
  })
})

describe('scan-time error screens', () => {
  const error = (status: number, code: string, extra: Record<string, unknown> = {}) => ({
    status,
    body: { detail: `detail for ${code}`, code, ...extra },
  })

  it.each([
    [
      error(404, 'qr_invalid'),
      'This code has expired',
      'Codes change every 30 seconds. Ask them to open My QR again, then scan the new code.',
      'Scan again',
    ],
    [
      error(409, 'cannot_verify_self'),
      "You can't record your own deed",
      'This QR belongs to your account, or to an account that shares your phone number. Scan the QR of the person who did the deed.',
      'Back to Record a deed',
    ],
    [
      error(409, 'already_recognized_today', { doer: AHMED }),
      "You've already recognised Ahmed today",
      'You can recognise the same person once a day. You can recognise Ahmed again tomorrow, or record someone else now.',
      'Record someone else',
    ],
    [
      error(409, 'already_scanned'),
      "You've already scanned this code",
      'Wait for their next code (it changes every 30 seconds), then scan again.',
      'Scan again',
    ],
    [
      error(400, 'outside_service_area'),
      "You're outside Bangalore",
      'Good Deeds is open in Bangalore only for now, within 40 km of the city centre.',
      null,
    ],
    [
      error(400, 'implausible_travel'),
      "We couldn't confirm your location",
      'Your location jumped further than anyone could travel. Turn off any app that changes your location, wait a minute and try again.',
      'Try again',
    ],
    [
      error(429, 'rate_limited'),
      'Too many tries',
      'Please wait a few minutes, then try again.',
      null,
    ],
    [
      error(429, 'pair_monthly_limit', { doer: AHMED }),
      "You've recorded Ahmed 15 times this month",
      "That's the most for one person. You can record Ahmed again from 1 December, and you can still record anyone else.",
      'Record someone else',
    ],
    [
      error(429, 'monthly_report_limit'),
      "You've recorded 150 deeds this month",
      "That's the monthly maximum. You can record more from 1 December.",
      null,
    ],
    [
      { status: 500, body: { detail: 'boom' } },
      'Something went wrong',
      'Check your internet connection and try again.',
      'Try again',
    ],
  ])('%#: %s → %s', async (reply, title, body, action) => {
    api(reply)
    renderApp('/report/code')
    await fillManual()

    const heading = await screen.findByRole('heading', { name: title })
    expect(heading).toHaveFocus()
    expect(screen.getByText(body)).toBeVisible()
    if (action) expect(screen.getByText(action)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to Home' })).toHaveAttribute('href', '/')
  })

  it('Try again goes back to the form', async () => {
    api([
      { status: 500, body: { detail: 'boom' } },
      { status: 201, body: makeScan() },
    ])
    const user = userEvent.setup()
    renderApp('/report/code')
    await fillManual()
    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    // The ID is shown normalised once it has been sent.
    expect(screen.getByLabelText('Their ID')).toHaveValue('GD-7K3M9Q0')
    expect(screen.getByLabelText('6-digit code')).toHaveValue('482 913')
  })

  it('links the actions to the right places', async () => {
    api(error(409, 'already_recognized_today', { doer: AHMED }))
    renderApp('/report/code')
    await fillManual()
    expect(await screen.findByRole('link', { name: 'Record someone else' })).toHaveAttribute(
      'href',
      '/report',
    )
  })
})

describe('/scan/:token (R5-L2: the token never stays in a URL)', () => {
  /** Start on the hub, then open the link, so Back shows what history holds. */
  async function openLink(token: string) {
    const rendered = renderApp('/report')
    await screen.findByRole('heading', { name: 'Record a deed' })
    await act(() => rendered.router.navigate(`/scan/${token}`))
    return rendered
  }

  it('moves the token into memory at once, checks it, and lands on the form', async () => {
    const mock = api()
    const { router } = await openLink('7K3M9Q0.123.abc')

    expect(await screen.findByText('Recognising')).toBeVisible()
    expect(mock.calls('POST /scans')[0].body).toEqual({
      token: '7K3M9Q0.123.abc',
      location: GOOD_FIX,
    })
    expect(router.state.location.pathname).toBe(`/report/scan/${SCAN_ID}`)
    expect(router.state.historyAction).toBe('REPLACE')
    // Back skips the link entirely.
    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe('/report')
  })

  it('replaces the URL before anything else, while asking for location', async () => {
    permissionMock.mockResolvedValue('prompt')
    const mock = api()
    const user = userEvent.setup()
    const { router } = await openLink('tok')
    await user.click(await screen.findByRole('button', { name: 'Allow location' }))
    // The explain screen is already on the token-free URL.
    expect(router.state.location.pathname).not.toContain('tok')
    expect(await screen.findByText('Recognising')).toBeVisible()
    expect(mock.calls('POST /scans')).toHaveLength(1)
  })

  it('keeps the token out of the URL on the error path, and Try again reuses it', async () => {
    const mock = api([
      { status: 500, body: { detail: 'boom' } },
      { status: 201, body: makeScan() },
    ])
    const user = userEvent.setup()
    const { router } = await openLink('tok-err')

    expect(await screen.findByRole('heading', { name: 'Something went wrong' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/report/link')
    expect(router.state.location.pathname).not.toContain('tok-err')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Recognising')).toBeVisible()
    expect(mock.calls('POST /scans').map((r) => r.body)).toEqual([
      { token: 'tok-err', location: GOOD_FIX },
      { token: 'tok-err', location: GOOD_FIX },
    ])
    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe('/report')
  })

  it('shows the expired-code screen for a stale link', async () => {
    api({ status: 404, body: { detail: 'Expired.', code: 'qr_invalid' } })
    const user = userEvent.setup()
    const { router } = await openLink('old')
    expect(await screen.findByRole('heading', { name: 'This code has expired' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/report/link')
    await user.click(screen.getByRole('button', { name: 'Scan again' }))
    expect(router.state.location.pathname).toBe('/report/scan')
  })

  it('asks for a new code when /report/link is opened without a token (e.g. a reload)', async () => {
    const mock = api()
    renderApp('/report/link')
    expect(await screen.findByRole('heading', { name: 'This code has expired' })).toBeVisible()
    expect(mock.calls('POST /scans')).toHaveLength(0)
  })

  it('sends a signed-out witness to log in with next=/report, never the token', async () => {
    mockApi()
    const { router } = renderApp('/scan/secret-token')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent('/report')}`)
    expect(router.state.location.search).not.toContain('secret-token')
    expect(window.sessionStorage.getItem('gd-rescan-notice')).toBe('1')
  })

  it('sends a witness who isn’t set up to onboarding with next=/report', async () => {
    mockApi(signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })))
    const { router } = renderApp('/scan/secret-token')
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent('/report')}`)
  })

  it('back on the hub, tells them to ask for a new code once', async () => {
    window.sessionStorage.setItem('gd-rescan-notice', '1')
    api()
    const { router } = renderApp('/report')
    const notice = await screen.findByText('This code has expired.')
    expect(notice.closest('[role="status"]')).toHaveTextContent(
      'This code has expired. Codes change every 30 seconds. Ask them to open My QR again, then scan the new code.',
    )
    await waitFor(() => expect(window.sessionStorage.getItem('gd-rescan-notice')).toBeNull())

    await act(() => router.navigate('/report/code'))
    await act(() => router.navigate('/report'))
    await screen.findByRole('heading', { name: 'Record a deed' })
    expect(screen.queryByText('This code has expired.')).not.toBeInTheDocument()
  })
})

describe('index.html (R5-L2)', () => {
  it('never sends a Referer', () => {
    expect(indexHtml).toContain('<meta name="referrer" content="no-referrer" />')
  })
})

describe('in-app camera', () => {
  type Detect = (source: unknown) => Promise<Array<{ rawValue: string }>>
  let detect: ReturnType<typeof vi.fn<Detect>>
  let stopTrack: ReturnType<typeof vi.fn>
  let getUserMedia: ReturnType<typeof vi.fn>

  beforeEach(() => {
    detect = vi.fn<Detect>().mockResolvedValue([])
    stopTrack = vi.fn()
    getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] })
    class FakeDetector {
      detect = detect
    }
    Object.defineProperty(window, 'BarcodeDetector', { value: FakeDetector, configurable: true })
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia },
      configurable: true,
    })
    Object.defineProperty(HTMLMediaElement.prototype, 'readyState', {
      get: () => 4,
      configurable: true,
    })
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      value: () => Promise.resolve(),
      configurable: true,
    })
  })

  afterEach(() => {
    Reflect.deleteProperty(window, 'BarcodeDetector')
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
    Reflect.deleteProperty(HTMLMediaElement.prototype, 'readyState')
  })

  it('opens the rear camera behind the location gate', async () => {
    api()
    renderApp('/report/scan')
    expect(await screen.findByRole('heading', { name: 'Scan their QR' })).toBeVisible()
    expect(screen.getByText('Location ready · ±35 m')).toBeVisible()
    expect(
      screen.getByText('Point your camera at the QR on their phone. It scans by itself.'),
    ).toBeVisible()
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled())
    expect(getUserMedia.mock.calls[0][0]).toEqual({
      video: { facingMode: 'environment' },
      audio: false,
    })
  })

  it('ignores a QR that isn’t Good Deeds, then checks a Good Deeds one', async () => {
    detect
      .mockResolvedValueOnce([{ rawValue: 'https://evil.example/scan/x' }])
      .mockResolvedValue([{ rawValue: `${window.location.origin}/scan/tok-1` }])
    const mock = api()
    const { router } = renderApp('/report/scan')

    expect(await screen.findByText('That’s not a Good Deeds QR')).toBeVisible()
    expect(await screen.findByText('Recognising', {}, { timeout: 3000 })).toBeVisible()
    expect(mock.calls('POST /scans')).toHaveLength(1)
    expect(mock.calls('POST /scans')[0].body).toEqual({ token: 'tok-1', location: GOOD_FIX })
    expect(router.state.location.pathname).toBe(`/report/scan/${SCAN_ID}`)
    // The camera was closed when Checking replaced it.
    expect(stopTrack).toHaveBeenCalled()
  })

  it('stops every camera track when closed', async () => {
    api()
    const user = userEvent.setup()
    const { router } = renderApp('/report/scan')
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled())
    await waitFor(() => expect(detect).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(router.state.location.pathname).toBe('/report')
    expect(stopTrack).toHaveBeenCalled()
  })

  it('links to the ID + code form', async () => {
    api()
    const user = userEvent.setup()
    const { router } = renderApp('/report/scan')
    await user.click(await screen.findByRole('button', { name: 'Enter ID and code instead' }))
    expect(router.state.location.pathname).toBe('/report/code')
  })

  it('falls back to the camera app when the camera is refused', async () => {
    getUserMedia.mockRejectedValue(new DOMException('no', 'NotAllowedError'))
    api()
    renderApp('/report/scan')
    expect(await screen.findByRole('heading', { name: 'Scan with your camera app' })).toBeVisible()
  })
})

describe('camera-app fallback', () => {
  it('explains how to scan with the phone’s camera when BarcodeDetector is missing', async () => {
    api()
    const user = userEvent.setup()
    const { router } = renderApp('/report/scan')

    expect(await screen.findByRole('heading', { name: 'Scan with your camera app' })).toBeVisible()
    expect(
      screen.getByText(
        'This browser can’t scan inside Good Deeds. Use your phone’s own camera instead. It opens Good Deeds and carries on from here.',
      ),
    ).toBeVisible()
    expect(screen.getByText('Tap the Good Deeds link that appears.')).toBeVisible()
    expect(screen.queryByText(/On a computer\?/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enter ID and code instead' }))
    expect(router.state.location.pathname).toBe('/report/code')
  })

  it('adds the computer note on a desktop', async () => {
    Object.defineProperty(window, 'matchMedia', {
      value: (query: string) => ({ matches: query.includes('pointer: fine'), media: query }),
      configurable: true,
    })
    try {
      api()
      renderApp('/report/scan')
      expect(
        await screen.findByText(
          'On a computer? Scanning works best on your phone. Or use their ID and code below.',
        ),
      ).toBeVisible()
    } finally {
      Reflect.deleteProperty(window, 'matchMedia')
    }
  })
})
