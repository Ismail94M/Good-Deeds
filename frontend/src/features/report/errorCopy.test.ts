import { describe, expect, it } from 'vitest'
import { ApiError } from '../../lib/api'
import { GeolocationError } from '../../lib/geolocation'
import {
  copyFor,
  errorCopy,
  errorKey,
  FORM_ERROR_KEYS,
  SCREEN_ERROR_KEYS,
  type ErrorCopyContext,
} from './errorCopy'

const AHMED = { handle: 'ahmed', display_name: 'Ahmed Khan' }
const OPTIONS = {
  reporter_monthly_limit: 150,
  reporter_doer_monthly_limit: 15,
  month_resets_at: '2026-12-01T00:00:00+05:30',
  scan_submit_minutes: 15,
  service_area_radius_km: 40,
  max_location_accuracy_m: 500,
}
const FULL: ErrorCopyContext = { doer: AHMED, options: OPTIONS, accuracyM: 850 }

const apiError = (status: number, code: string | null, extra: Record<string, unknown> = {}) =>
  new ApiError(status, `detail for ${code}`, code, {}, extra)

describe('errorKey', () => {
  it('uses the API code when it has copy', () => {
    expect(errorKey(apiError(429, 'pair_monthly_limit'))).toBe('pair_monthly_limit')
    expect(errorKey(apiError(404, 'doer_code_invalid'))).toBe('doer_code_invalid')
    expect(errorKey(apiError(400, 'implausible_time'))).toBe('implausible_time')
  })

  it('treats an uncoded 429 as rate limited', () => {
    expect(errorKey(apiError(429, null))).toBe('rate_limited')
  })

  it('falls back to generic for unknown codes, 5xx and network failures', () => {
    expect(errorKey(apiError(500, null))).toBe('generic')
    expect(errorKey(apiError(422, 'validation_error'))).toBe('generic')
    expect(errorKey(new TypeError('Failed to fetch'))).toBe('generic')
    expect(errorKey(undefined)).toBe('generic')
  })

  it('maps location failures', () => {
    expect(errorKey(new GeolocationError('denied', 'x'))).toBe('location_denied')
    expect(errorKey(new GeolocationError('timeout', 'x'))).toBe('location_unavailable')
    expect(errorKey(new GeolocationError('unavailable', 'x'))).toBe('location_unavailable')
  })
})

describe('copyFor: the F3 copy sheet', () => {
  // Title, body, action label as on docs/design/f3 errList() (sample doer Ahmed, 1 December),
  // with straight apostrophes and the doc's interim "them" where the doer is unknown.
  it.each([
    [
      'qr_invalid',
      'screen',
      'This code has expired',
      'Codes change every 30 seconds. Ask them to open My QR again, then scan the new code.',
      'Scan again',
    ],
    [
      'doer_code_invalid',
      'inline',
      "ID and code don't match",
      'Codes change every 30 seconds. Ask for the code on their screen right now and try again.',
      'Continue',
    ],
    [
      'cannot_verify_self',
      'screen',
      "You can't record your own deed",
      'This QR belongs to your account, or to an account that shares your phone number. Scan the QR of the person who did the deed.',
      'Back to Record a deed',
    ],
    [
      'already_recognized_today',
      'screen',
      "You've already recognised Ahmed today",
      'You can recognise the same person once a day. You can recognise Ahmed again tomorrow, or record someone else now.',
      'Record someone else',
    ],
    [
      'already_scanned',
      'screen',
      "You've already scanned this code",
      'Wait for their next code (it changes every 30 seconds), then scan again.',
      'Scan again',
    ],
    [
      'outside_service_area',
      'screen',
      "You're outside Bangalore",
      'Good Deeds is open in Bangalore only for now, within 40 km of the city centre.',
      'Back to Home',
    ],
    [
      'location_too_imprecise',
      'location',
      "Your location isn't precise enough",
      'Your phone places you somewhere within 850 m. We need it within 500 m.',
      'Try again',
    ],
    [
      'implausible_travel',
      'screen',
      "We couldn't confirm your location",
      'Your location jumped further than anyone could travel. Turn off any app that changes your location, wait a minute and try again.',
      'Try again',
    ],
    [
      'rate_limited',
      'screen',
      'Too many tries',
      'Please wait a few minutes, then try again.',
      'Back to Home',
    ],
    [
      'pair_monthly_limit',
      'screen',
      "You've recorded Ahmed 15 times this month",
      "That's the most for one person. You can record Ahmed again from 1 December, and you can still record anyone else.",
      'Record someone else',
    ],
    [
      'monthly_report_limit',
      'screen',
      "You've recorded 150 deeds this month",
      "That's the monthly maximum. You can record more from 1 December.",
      'Back to Home',
    ],
    [
      'scan_expired',
      'screen',
      'Time ran out for this report',
      "Reports must be sent within 15 minutes of scanning. Scan Ahmed's QR again to start over.",
      'Scan again',
    ],
    [
      'scan_used',
      'screen',
      'This report was already sent',
      "You'll find it in My deeds.",
      'See my reports',
    ],
    [
      'location_denied',
      'location',
      'Location is blocked',
      "We can't verify a deed without your location. You can turn it back on in a few taps.",
      'Try again',
    ],
    [
      'scan_not_found',
      'screen',
      "This report link isn't valid",
      'Start again from Record a deed.',
      'Record a deed',
    ],
    [
      'generic',
      'screen',
      'Something went wrong',
      'Check your internet connection and try again.',
      'Try again',
    ],
  ] as const)('%s', (key, placement, title, body, label) => {
    const copy = copyFor(key, FULL)
    expect(copy).toMatchObject({ key, placement, title, body })
    expect(copy.action?.label).toBe(label)
  })

  it('gives every screen key a title, body and action', () => {
    for (const key of SCREEN_ERROR_KEYS) {
      const copy = copyFor(key, FULL)
      expect(copy.title, key).not.toBe('')
      expect(copy.body, key).not.toBe('')
      expect(copy.action, key).not.toBeNull()
    }
  })

  it('maps actions to where they lead', () => {
    expect(copyFor('qr_invalid').action?.kind).toBe('scan_again')
    expect(copyFor('doer_code_invalid').action?.kind).toBe('retry')
    expect(copyFor('pair_monthly_limit').action?.kind).toBe('record_hub')
    expect(copyFor('rate_limited').action?.kind).toBe('home')
    expect(copyFor('scan_used').action?.kind).toBe('my_reports')
  })

  it('uses the canvas colours: blue for limits and today, amber for expiry', () => {
    expect(copyFor('already_recognized_today').tone).toBe('info')
    expect(copyFor('pair_monthly_limit').tone).toBe('info')
    expect(copyFor('qr_invalid').tone).toBe('warning')
    expect(copyFor('scan_expired').tone).toBe('warning')
  })

  it('follows config for the numbers in the copy', () => {
    const options = {
      ...OPTIONS,
      reporter_monthly_limit: 200,
      reporter_doer_monthly_limit: 20,
      scan_submit_minutes: 10,
      service_area_radius_km: 25,
    }
    const context = { doer: AHMED, options }
    expect(copyFor('monthly_report_limit', context).title).toBe(
      "You've recorded 200 deeds this month",
    )
    expect(copyFor('pair_monthly_limit', context).title).toBe(
      "You've recorded Ahmed 20 times this month",
    )
    expect(copyFor('scan_expired', context).body).toMatch(/^Reports must be sent within 10 minutes/)
    expect(copyFor('outside_service_area', context).body).toMatch(/within 25 km/)
  })

  it("prefers the scan's reset date over the options' one", () => {
    const copy = copyFor('pair_monthly_limit', {
      ...FULL,
      monthResetsAt: '2027-01-01T00:00:00+05:30',
    })
    expect(copy.body).toMatch(/again from 1 January/)
  })
})

describe('copyFor: without context', () => {
  it('drops numbers, names and dates it does not know instead of guessing', () => {
    expect(copyFor('already_recognized_today')).toMatchObject({
      title: "You've already recognised this person today",
      body: 'You can recognise the same person once a day. You can recognise them again tomorrow, or record someone else now.',
    })
    expect(copyFor('pair_monthly_limit')).toMatchObject({
      title: "You've recorded this person as many times as you can this month",
      body: "That's the most for one person. You can record them again next month, and you can still record anyone else.",
    })
    expect(copyFor('monthly_report_limit')).toMatchObject({
      title: "You've reached this month's limit",
      body: "That's the monthly maximum. You can record more next month.",
    })
    expect(copyFor('scan_expired').body).toBe(
      'Reports must be sent soon after scanning. Scan their QR again to start over.',
    )
    expect(copyFor('outside_service_area').body).toBe(
      'Good Deeds is open in Bangalore only for now.',
    )
    expect(copyFor('location_too_imprecise').body).toBe(
      'We need a more precise location to check this report.',
    )
    expect(copyFor('location_too_imprecise', { options: OPTIONS }).body).toBe(
      'We need your location to within 500 m.',
    )
  })
})

describe('errorCopy', () => {
  it('names the doer from the scan-time error body (D27)', () => {
    const error = apiError(429, 'pair_monthly_limit', { doer: AHMED })
    expect(errorCopy(error, { options: OPTIONS }).title).toBe(
      "You've recorded Ahmed 15 times this month",
    )
    const today = apiError(409, 'already_recognized_today', { doer: AHMED })
    expect(errorCopy(today).title).toBe("You've already recognised Ahmed today")
  })

  it("prefers the screen's doer (the scan) over the body", () => {
    const error = apiError(409, 'already_recognized_today', {
      doer: { handle: 'x', display_name: 'Someone Else' },
    })
    expect(errorCopy(error, { doer: AHMED }).title).toBe("You've already recognised Ahmed today")
  })

  it.each(FORM_ERROR_KEYS)('shows the API detail inline for %s', (code) => {
    const copy = errorCopy(new ApiError(400, 'Choose when it happened.', code))
    expect(copy).toEqual({
      key: code,
      placement: 'form',
      tone: 'warning',
      title: 'Choose when it happened.',
      body: '',
      action: null,
    })
  })

  it('uses generic copy for a network failure', () => {
    expect(errorCopy(new TypeError('Failed to fetch')).title).toBe('Something went wrong')
  })

  it('uses the location copy for a location failure', () => {
    expect(errorCopy(new GeolocationError('denied', 'x')).title).toBe('Location is blocked')
    expect(errorCopy(new GeolocationError('timeout', 'x')).title).toBe(
      "We couldn't find your location",
    )
  })
})
