import { descriptionError } from '../deeds/formValues'

/** Used when `/deeds/options` has no `description_min` (the deed forms' minimum). */
export const DEFAULT_DESCRIPTION_MIN = 10

/** Date and time inputs of the Log a deed form (M2-DESIGN §5.3), in the phone's own time. */

const pad = (n: number) => String(n).padStart(2, '0')

/** "2026-11-17" for `<input type="date">`. */
export function toDateInput(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** "07:15" for `<input type="time">`. */
export function toTimeInput(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** The local date and time as a Date, or null when either is empty or invalid. */
export function fromInputs(date: string, time: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  const timeMatch = /^(\d{2}):(\d{2})$/.exec(time)
  if (!match || !timeMatch) return null
  const value = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(timeMatch[1]),
    Number(timeMatch[2]),
  )
  return Number.isNaN(value.getTime()) ? null : value
}

/** Interim copy (not on the canvas) for the self-log form's checks. */
export const SELF_LOG_ERRORS = {
  category: 'Choose what kind of deed it was.',
  when: 'Choose a date and time.',
  future: 'Choose a time that has already passed.',
} as const

export type SelfLogErrors = Partial<Record<keyof typeof SELF_LOG_ERRORS | 'description', string>>

export function validateSelfLog(
  values: { category: string | null; description: string; date: string; time: string },
  now: Date = new Date(),
  descriptionMin: number = DEFAULT_DESCRIPTION_MIN,
): SelfLogErrors {
  const errors: SelfLogErrors = {}
  if (!values.category) errors.category = SELF_LOG_ERRORS.category
  // Like the deed forms: "Write at least {min} characters so people understand what happened."
  const description = descriptionError(values.description, descriptionMin)
  if (description) errors.description = description
  const when = fromInputs(values.date, values.time)
  if (!when) errors.when = SELF_LOG_ERRORS.when
  else if (when.getTime() > now.getTime()) errors.when = SELF_LOG_ERRORS.future
  return errors
}
