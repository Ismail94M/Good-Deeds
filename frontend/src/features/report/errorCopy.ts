/**
 * Error code → copy for every Record a deed screen (M2-DESIGN §5.4). The copy is the F3 canvas's
 * copy sheet (`docs/design/f3/Main.dc.html`, `errList()`), with the doc's interim copy for the
 * cases the canvas doesn't draw. Numbers and dates come from config and the server, never from
 * constants here.
 */

import { ApiError } from '../../lib/api'
import { GeolocationError } from '../../lib/geolocation'
import { errorDoer, type DeedOptions, type UserSummary } from '../deeds/deedsApi'
import { firstName, formatResetDate } from './format'

/** API codes with their own copy, plus the browser location failures and a catch-all. */
export const SCREEN_ERROR_KEYS = [
  'qr_invalid',
  'doer_code_invalid',
  'cannot_verify_self',
  'already_recognized_today',
  'already_scanned',
  'outside_service_area',
  'location_too_imprecise',
  'implausible_travel',
  'rate_limited',
  'pair_monthly_limit',
  'monthly_report_limit',
  'scan_expired',
  'scan_used',
  'scan_not_found',
  'location_denied',
  'location_unavailable',
  'generic',
] as const

/** Validation codes shown inline on the deed form with the API's own `detail`. */
export const FORM_ERROR_KEYS = [
  'invalid_category',
  'invalid_description',
  'invalid_beneficiaries',
  'implausible_time',
] as const

export type ScreenErrorKey = (typeof SCREEN_ERROR_KEYS)[number]
export type FormErrorKey = (typeof FORM_ERROR_KEYS)[number]
export type ErrorKey = ScreenErrorKey | FormErrorKey

/**
 * Where the error appears: a full-screen error view, inline on the ID + code form, on the
 * location screen (with its tips and Try again), or inline on the deed form.
 */
export type ErrorPlacement = 'screen' | 'inline' | 'location' | 'form'

/**
 * What the primary button does; the screen maps it to a route or a handler.
 * `scan_again` → `/report/scan`, `retry` → repeat the failed step, `record_hub` → `/report`,
 * `home` → `/`, `my_reports` → `/me/deeds?tab=reported`.
 */
export type ErrorActionKind = 'scan_again' | 'retry' | 'record_hub' | 'home' | 'my_reports'

export type ErrorCopy = {
  key: ErrorKey
  placement: ErrorPlacement
  /** Icon colours on the full-screen view: amber `warning`, blue `info` (as on the canvas). */
  tone: 'warning' | 'info'
  title: string
  /** Empty for form errors, whose `title` is the API's message. */
  body: string
  action: { kind: ErrorActionKind; label: string } | null
}

export type ErrorCopyContext = {
  /** The doer when the screen already knows them (the deed form knows from the scan). */
  doer?: UserSummary | null
  /** `GET /deeds/options`, for the limits in the copy. */
  options?: Partial<
    Pick<
      DeedOptions,
      | 'reporter_monthly_limit'
      | 'reporter_doer_monthly_limit'
      | 'month_resets_at'
      | 'scan_submit_minutes'
      | 'service_area_radius_km'
      | 'max_location_accuracy_m'
    >
  > | null
  /** The scan's `month_resets_at`; preferred over the options' copy of the same date. */
  monthResetsAt?: string | null
  /** The position's accuracy in metres, for `location_too_imprecise`. */
  accuracyM?: number | null
}

const SCREEN_KEYS: ReadonlySet<string> = new Set(SCREEN_ERROR_KEYS)
const FORM_KEYS: ReadonlySet<string> = new Set(FORM_ERROR_KEYS)

/** The copy key for any thrown value: an API error code, a location failure, or `generic`. */
export function errorKey(error: unknown): ErrorKey {
  if (error instanceof GeolocationError) {
    return error.kind === 'denied' ? 'location_denied' : 'location_unavailable'
  }
  if (!(error instanceof ApiError)) return 'generic'
  const code = error.code
  if (code && (SCREEN_KEYS.has(code) || FORM_KEYS.has(code))) return code as ErrorKey
  // A 429 without a code (e.g. from a proxy) reads the same as our own rate limit.
  if (error.status === 429) return 'rate_limited'
  return 'generic'
}

const action = (kind: ErrorActionKind, label: string) => ({ kind, label })

/** Copy for a thrown value (API error, `GeolocationError` or anything else). */
export function errorCopy(error: unknown, context: ErrorCopyContext = {}): ErrorCopy {
  const key = errorKey(error)
  const doer = context.doer ?? errorDoer(error)
  const detail = error instanceof ApiError ? error.detail : null
  return copyFor(key, { ...context, doer }, detail)
}

/**
 * Copy for a key. `detail` is the API's message, used only by the form errors. Missing context
 * values drop the sentence or number that needs them instead of guessing a value.
 */
export function copyFor(
  key: ErrorKey,
  context: ErrorCopyContext = {},
  detail: string | null = null,
): ErrorCopy {
  const { options } = context
  const first = context.doer ? firstName(context.doer.display_name) : null
  const date = formatResetDate(context.monthResetsAt ?? options?.month_resets_at)

  switch (key) {
    case 'qr_invalid':
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: 'This code has expired',
        // Interim: the canvas names the doer, but a bad code tells us nothing about them.
        body: 'Codes change every 30 seconds. Ask them to open My QR again, then scan the new code.',
        action: action('scan_again', 'Scan again'),
      }
    case 'doer_code_invalid':
      return {
        key,
        placement: 'inline',
        tone: 'warning',
        title: "ID and code don't match",
        body: 'Codes change every 30 seconds. Ask for the code on their screen right now and try again.',
        action: action('retry', 'Continue'),
      }
    case 'cannot_verify_self':
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: "You can't record your own deed",
        body: 'This QR belongs to your account, or to an account that shares your phone number. Scan the QR of the person who did the deed.',
        action: action('record_hub', 'Back to Record a deed'),
      }
    case 'already_recognized_today':
      return {
        key,
        placement: 'screen',
        tone: 'info',
        title: `You've already recognised ${first ?? 'this person'} today`,
        body: `You can recognise the same person once a day. You can recognise ${first ?? 'them'} again tomorrow, or record someone else now.`,
        action: action('record_hub', 'Record someone else'),
      }
    case 'already_scanned':
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: "You've already scanned this code",
        // Interim: "their" for the canvas's "Ahmed's" (the doer is unknown at this point).
        body: 'Wait for their next code (it changes every 30 seconds), then scan again.',
        action: action('scan_again', 'Scan again'),
      }
    case 'outside_service_area': {
      const radius = options?.service_area_radius_km
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: "You're outside Bangalore",
        body:
          radius != null
            ? `Good Deeds is open in Bangalore only for now, within ${radius} km of the city centre.`
            : 'Good Deeds is open in Bangalore only for now.',
        action: action('home', 'Back to Home'),
      }
    }
    case 'location_too_imprecise': {
      const max = options?.max_location_accuracy_m
      const accuracy = context.accuracyM
      const body =
        accuracy != null && max != null
          ? `Your phone places you somewhere within ${accuracy} m. We need it within ${max} m.`
          : max != null
            ? `We need your location to within ${max} m.`
            : 'We need a more precise location to check this report.'
      return {
        key,
        placement: 'location',
        tone: 'warning',
        title: "Your location isn't precise enough",
        body,
        action: action('retry', 'Try again'),
      }
    }
    case 'implausible_travel':
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: "We couldn't confirm your location",
        body: 'Your location jumped further than anyone could travel. Turn off any app that changes your location, wait a minute and try again.',
        action: action('retry', 'Try again'),
      }
    case 'rate_limited':
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: 'Too many tries',
        body: 'Please wait a few minutes, then try again.',
        action: action('home', 'Back to Home'),
      }
    case 'pair_monthly_limit': {
      const limit = options?.reporter_doer_monthly_limit
      const who = first ?? 'this person'
      const again = date ? `again from ${date}` : 'again next month'
      return {
        key,
        placement: 'screen',
        tone: 'info',
        title:
          limit != null
            ? `You've recorded ${who} ${limit} times this month`
            : `You've recorded ${who} as many times as you can this month`,
        body: `That's the most for one person. You can record ${first ?? 'them'} ${again}, and you can still record anyone else.`,
        action: action('record_hub', 'Record someone else'),
      }
    }
    case 'monthly_report_limit': {
      const limit = options?.reporter_monthly_limit
      return {
        key,
        placement: 'screen',
        tone: 'info',
        title:
          limit != null
            ? `You've recorded ${limit} deeds this month`
            : "You've reached this month's limit",
        body: `That's the monthly maximum. You can record more ${date ? `from ${date}` : 'next month'}.`,
        action: action('home', 'Back to Home'),
      }
    }
    case 'scan_expired': {
      const minutes = options?.scan_submit_minutes
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: 'Time ran out for this report',
        body: `${
          minutes != null
            ? `Reports must be sent within ${minutes} minutes of scanning.`
            : 'Reports must be sent soon after scanning.'
        } Scan ${first ? `${first}'s` : 'their'} QR again to start over.`,
        action: action('scan_again', 'Scan again'),
      }
    }
    case 'scan_used':
      return {
        key,
        placement: 'screen',
        tone: 'info',
        title: 'This report was already sent',
        body: "You'll find it in My deeds.",
        action: action('my_reports', 'See my reports'),
      }
    case 'scan_not_found':
      // Interim copy (§5.4, §9 Q19).
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: "This report link isn't valid",
        body: 'Start again from Record a deed.',
        action: action('record_hub', 'Record a deed'),
      }
    case 'location_denied':
      return {
        key,
        placement: 'location',
        tone: 'warning',
        title: 'Location is blocked',
        body: "We can't verify a deed without your location. You can turn it back on in a few taps.",
        action: action('retry', 'Try again'),
      }
    case 'location_unavailable':
      // Interim copy (§5.3): the not-precise layout with its own title.
      return {
        key,
        placement: 'location',
        tone: 'warning',
        title: "We couldn't find your location",
        body: "Check that Location is switched on in your phone's settings.",
        action: action('retry', 'Try again'),
      }
    case 'invalid_category':
    case 'invalid_description':
    case 'invalid_beneficiaries':
    case 'implausible_time':
      return {
        key,
        placement: 'form',
        tone: 'warning',
        title: detail ?? 'Please check the form and try again.',
        body: '',
        action: null,
      }
    case 'generic':
      // Interim copy (§5.4): network failure, 5xx or an unexpected code.
      return {
        key,
        placement: 'screen',
        tone: 'warning',
        title: 'Something went wrong',
        body: 'Check your internet connection and try again.',
        action: action('retry', 'Try again'),
      }
  }
}
