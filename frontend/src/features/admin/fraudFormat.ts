/**
 * Helpers for the fraud admin screens (M5-DESIGN §6.2): formatting, the note check and the error
 * copy for the fraud endpoints (§3.3).
 */

import { useState } from 'react'
import { ApiError, errorMessage } from '../../lib/api'
import { LAUNCH_TIME_ZONE } from '../report/format'
import type { FraudOptions, UserRef } from './fraudApi'

export const DEFAULT_NOTE_MIN = 5
export const DEFAULT_NOTE_MAX = 2000

export const KIND_LABELS = { case: 'Case decision', override: 'Points change' } as const

const HOUR = 60 * 60 * 1000

// ----------------------------------------------------------------- formatting

const shortDate = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: LAUNCH_TIME_ZONE,
})

/** "18 Nov 2026" in IST. */
export function formatShortDate(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : shortDate.format(date)
}

const monthName = new Intl.DateTimeFormat('en-GB', {
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})

/** "2026-11" → "November 2026". */
export function formatMonth(period: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(period)
  if (!match) return period
  return monthName.format(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 15)))
}

/** "Priya", "Priya and Ahmed", "Priya, Ahmed and Ravi", "Priya, Ahmed, Ravi +2". */
export function namesLine(users: UserRef[], total: number, max = 3): string {
  const names = users.slice(0, max).map((u) => u.display_name || `@${u.handle}`)
  const more = total - names.length
  if (more > 0) return `${names.join(', ')} +${more}`
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** True when `iso` is less than 48 hours away (or already passed): the deadline chip goes red. */
export function isUrgent(iso: string, now: number = Date.now()): boolean {
  const at = Date.parse(iso)
  return !Number.isNaN(at) && at - now < 48 * HOUR
}

// ----------------------------------------------------------------- errors

/** Friendly copy for the fraud endpoints' error codes (§3.3). */
export function fraudError(error: unknown, options?: FraudOptions): string {
  if (!(error instanceof ApiError)) return errorMessage(error)
  const min = options?.note_min_chars ?? DEFAULT_NOTE_MIN
  const max = options?.note_max_chars ?? DEFAULT_NOTE_MAX
  switch (error.code) {
    case 'explanation_required':
      return 'Ask the people involved for an explanation first. Everyone whose deeds you exclude or reject needs to be asked.'
    case 'explanation_pending':
      return `${error.detail} You can confirm once they reply or the date has passed.`
    case 'case_resolved':
      return 'This case has already been decided. The page has been reloaded.'
    case 'case_not_open':
    case 'case_not_parked':
      return 'This case changed while you were looking. The page has been reloaded.'
    case 'use_dismiss':
      return 'Every deed is set to Release. Choose another outcome for at least one deed, or dismiss the case instead.'
    case 'deed_actions_invalid':
      return 'The deeds in this case changed. Reload the page and choose an outcome for each deed again.'
    case 'note_required':
      return `Write a note of at least ${min} characters.`
    case 'note_too_long':
      return `Keep the note to ${max} characters or fewer.`
    case 'already_requested':
      return 'Some of these people have already been asked. The page has been reloaded.'
    case 'not_a_subject':
      return 'Choose people who are part of this case.'
    case 'appeal_decided':
      return 'This appeal has already been decided. The page has been reloaded.'
    case 'case_not_found':
    case 'appeal_not_found':
      return 'This isn’t available any more. Go back to the list.'
    case 'user_not_found':
      return 'No one has that handle. Check it and try again.'
    case 'invalid_cursor':
      return 'This list changed. Reload the page to see it again.'
    case 'rate_limited':
      return 'You’re going very fast. Wait a minute, then try again.'
  }
  if (error.status === 422) return error.fieldErrors.note ?? error.detail
  return error.detail
}

/** Codes after which the page reloads the case (or appeal) to show its current state. */
export const RELOAD_CODES = new Set([
  'case_resolved',
  'case_not_open',
  'case_not_parked',
  'already_requested',
  'appeal_decided',
])

// ----------------------------------------------------------------- note

/** Note-length check shared by every dialog (min from the options). */
export function useNote(options: FraudOptions | undefined) {
  const [note, setNote] = useState('')
  const [invalid, setInvalid] = useState(false)
  const min = options?.note_min_chars ?? DEFAULT_NOTE_MIN
  return {
    note,
    setNote,
    invalid,
    tooShortMessage: `Write a note of at least ${min} characters.`,
    /** The trimmed note, or null (and the field marked) when it is too short. */
    check: (): string | null => {
      const text = note.trim()
      const ok = text.length >= min
      setInvalid(!ok)
      return ok ? text : null
    },
  }
}
