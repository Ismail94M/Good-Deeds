/** Client-side checks that mirror backend/app/modules/identity/schemas.py. */

export const MIN_AGE = 18
export const UNDERAGE_MESSAGE =
  'Sorry, you must be 18 or older to use Good Deeds. We hope to see you when you turn 18!'

const RESERVED_HANDLES = new Set([
  'admin',
  'api',
  'app',
  'gooddeeds',
  'help',
  'login',
  'logout',
  'me',
  'moderator',
  'root',
  'settings',
  'signup',
  'support',
  'system',
])

export function validateDisplayName(value: string): string | null {
  const v = value.trim()
  if (!v) return 'Please enter your name.'
  if (v.length > 60) return 'Please keep your name under 60 characters.'
  return null
}

export function validateHandle(value: string): string | null {
  const v = value.trim()
  if (!v) return 'Please choose a handle.'
  if (v.length < 3 || v.length > 30) return 'Your handle needs 3 to 30 characters.'
  if (!/^[A-Za-z0-9_.]+$/.test(v)) return 'Use only letters, numbers, _ and . in your handle.'
  if (v.startsWith('.') || v.endsWith('.') || v.includes('..'))
    return "Dots can't be at the start, end, or next to each other."
  if (RESERVED_HANDLES.has(v.toLowerCase())) return 'This handle is reserved. Please pick another.'
  return null
}

export function validateEmail(value: string): string | null {
  const v = value.trim()
  if (!v) return 'Please enter your email.'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return 'Please enter a valid email address.'
  return null
}

export function validatePassword(value: string): string | null {
  if (!value) return 'Please choose a password.'
  if (value.length < 10) return 'Your password needs at least 10 characters.'
  if (value.length > 128) return 'Please keep your password under 128 characters.'
  return null
}

/** Full years between a YYYY-MM-DD birth date and `today`. */
export function ageOn(dateOfBirth: string, today: Date = new Date()): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateOfBirth)
  if (!match) return null
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const todayMonth = today.getMonth() + 1
  const todayDay = today.getDate()
  const beforeBirthday = todayMonth < month || (todayMonth === month && todayDay < day)
  return today.getFullYear() - year - (beforeBirthday ? 1 : 0)
}

export function validateDateOfBirth(value: string, today: Date = new Date()): string | null {
  if (!value) return 'Please enter your date of birth.'
  const age = ageOn(value, today)
  if (age === null || age > 120) return 'Please enter a valid date of birth.'
  if (age < 0) return 'Please enter a valid date of birth.'
  if (age < MIN_AGE) return UNDERAGE_MESSAGE
  return null
}

export type FieldErrors<K extends string> = Partial<Record<K, string>>

/** Keep only fields that have an error. */
export function collectErrors<K extends string>(checks: Record<K, string | null>): FieldErrors<K> {
  const out: FieldErrors<K> = {}
  for (const key of Object.keys(checks) as K[]) {
    const message = checks[key]
    if (message) out[key] = message
  }
  return out
}

/** Safe in-app redirect target from a ?next= value (never another origin). */
export function safeNext(next: string | null, fallback = '/'): string {
  if (next && next.startsWith('/') && !next.startsWith('//') && !next.includes('\\')) {
    return next
  }
  return fallback
}

export type ConsentValues = {
  date_of_birth: string
  accept_terms: boolean
  accept_privacy: boolean
}

export type ConsentErrors = Partial<Record<keyof ConsentValues, string>>

export function consentErrors(values: ConsentValues, today: Date = new Date()): ConsentErrors {
  const errors: ConsentErrors = {}
  const dob = validateDateOfBirth(values.date_of_birth, today)
  if (dob) errors.date_of_birth = dob
  if (!values.accept_terms) errors.accept_terms = 'Please agree to the Terms to continue.'
  if (!values.accept_privacy)
    errors.accept_privacy = 'Please confirm you have read the Privacy Notice.'
  return errors
}
