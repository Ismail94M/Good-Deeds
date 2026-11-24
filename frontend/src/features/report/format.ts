/** Small text helpers shared by the Record a deed screens (M2-DESIGN §5.3, §5.4). */

/** The launch city's time zone. Reset dates are shown in IST whatever the phone's zone is. */
export const LAUNCH_TIME_ZONE = 'Asia/Kolkata'

const resetDateFormat = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'long',
  timeZone: LAUNCH_TIME_ZONE,
})

/**
 * "1 December" from the API's `month_resets_at` (00:00 IST on the 1st). Formatted in IST, so a
 * phone in another zone still shows the server's date. Null when missing or unparseable.
 */
export function formatResetDate(iso: string | null | undefined): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return resetDateFormat.format(date)
}

/** "Ahmed" from "Ahmed Khan", for short sentences. Falls back to the whole name. */
export function firstName(displayName: string): string {
  const first = displayName.trim().split(/\s+/)[0]
  return first || displayName.trim()
}

const istDayKey = new Intl.DateTimeFormat('en-CA', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  timeZone: LAUNCH_TIME_ZONE,
})

const istTime = new Intl.DateTimeFormat('en-IN', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
  timeZone: LAUNCH_TIME_ZONE,
})

const istDayMonth = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'long',
  timeZone: LAUNCH_TIME_ZONE,
})

/** "4:12 pm" in IST. */
function timeOfDay(date: Date): string {
  const parts = istTime.formatToParts(date)
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? ''
  return `${get('hour')}:${get('minute')} ${get('dayPeriod').toLowerCase()}`
}

/**
 * When a deed happened, as on the result summary: "Today, 4:12 pm", "Yesterday, 9:05 am" or
 * "14 November, 6:30 pm", in IST like the reset dates. Empty when unparseable.
 */
export function formatOccurredAt(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const day = istDayKey.format(date)
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000)
  const label =
    day === istDayKey.format(now)
      ? 'Today'
      : day === istDayKey.format(yesterday)
        ? 'Yesterday'
        : istDayMonth.format(date)
  return `${label}, ${timeOfDay(date)}`
}

/** "15 minutes", "1 hour", "2 hours", "90 minutes": for "Up to 2 hours before you scanned". */
export function formatMinutes(minutes: number): string {
  if (minutes >= 60 && minutes % 60 === 0) {
    const hours = minutes / 60
    return `${hours} ${hours === 1 ? 'hour' : 'hours'}`
  }
  return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
}

/** "AK" from "Ahmed Khan", for the initials avatar. */
export function initials(displayName: string): string {
  const words = displayName.trim().split(/\s+/).filter(Boolean)
  const letters = words.length > 1 ? [words[0], words[words.length - 1]] : words
  return letters
    .map((word) => Array.from(word)[0] ?? '')
    .join('')
    .toUpperCase()
}

const istShortDate = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  timeZone: LAUNCH_TIME_ZONE,
})

/** "16 Nov" in IST (withdrawn notice, card dates). */
export function formatShortDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : istShortDate.format(date)
}

/** Card date: "Today, 8:05 am", "Yesterday, 9:05 am" or "14 Nov" (F6 canvas). */
export function formatCardDate(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const day = istDayKey.format(date)
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000)
  if (day === istDayKey.format(now)) return `Today, ${timeOfDay(date)}`
  if (day === istDayKey.format(yesterday)) return `Yesterday, ${timeOfDay(date)}`
  return formatShortDate(iso)
}
