/** Copy and small formatters for the photo screens (docs/M3-DESIGN.md §6.3). */

import { LAUNCH_TIME_ZONE } from '../report/format'
import type { MediaOptions, PhotoReportReason, ReportPriority } from './mediaApi'

const TYPE_NAMES: Record<string, string> = {
  'image/jpeg': 'JPG',
  'image/png': 'PNG',
  'image/webp': 'WebP',
}

/** "JPG, PNG or WebP" from the allowed types. */
function typeList(types: string[]): string {
  const names = types.map((type) => TYPE_NAMES[type] ?? type.replace(/^image\//, '').toUpperCase())
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} or ${names[names.length - 1]}`
}

/** "That file can't be used. Choose a JPG, PNG or WebP photo under 10 MB." (size from options). */
export function fileRefusedCopy(
  options: Pick<MediaOptions, 'upload_allowed_types' | 'upload_max_bytes'>,
): string {
  const mb = Math.max(1, Math.round(options.upload_max_bytes / (1024 * 1024)))
  return `That file can’t be used. Choose a ${typeList(options.upload_allowed_types)} photo under ${mb} MB.`
}

/** Photo report reasons as reporters see them (§6.3 "Report a photo"); options override these. */
export const REASON_LABELS: Record<PhotoReportReason, string> = {
  not_consented: 'I’m in this photo and didn’t agree',
  child_no_guardian_consent: 'A child is shown without a parent’s consent',
  private_details: 'It shows private details (like a phone number or address)',
  inappropriate: 'It’s inappropriate or harmful',
  other: 'Something else',
  impersonation: 'Not this person / impersonation',
}

/** Q44: child first; privacy and harm equal; then the rest. */
export const REASON_PRIORITY: Record<PhotoReportReason, ReportPriority> = {
  child_no_guardian_consent: 1,
  not_consented: 2,
  private_details: 2,
  inappropriate: 2,
  other: 3,
  impersonation: 2,
}

/** The reason line in the uploader's "removed after review" banner (§6.3). */
export const REMOVAL_REASON_TEXT: Record<PhotoReportReason, string> = {
  not_consented: 'someone shown in it hadn’t agreed to appear',
  child_no_guardian_consent: 'it showed a child without a parent’s consent',
  private_details: 'it showed private details',
  inappropriate: 'it didn’t follow our photo guidelines',
  other: 'it didn’t follow our photo guidelines',
  impersonation: 'it didn’t show the account holder',
}

export function photoCount(n: number): string {
  return n === 1 ? '1 photo' : `${n} photos`
}

const dayKey = new Intl.DateTimeFormat('en-CA', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  timeZone: LAUNCH_TIME_ZONE,
})
const timeParts = new Intl.DateTimeFormat('en-IN', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
  timeZone: LAUNCH_TIME_ZONE,
})
const dayMonth = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'long',
  timeZone: LAUNCH_TIME_ZONE,
})

function clock(date: Date): string {
  const parts = timeParts.formatToParts(date)
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? ''
  return `${get('hour')}:${get('minute')} ${get('dayPeriod').toLowerCase()}`
}

/**
 * A deadline in IST: "4:12 pm today", "4:12 pm tomorrow" or "21 November, 4:12 pm".
 * Empty when unparseable.
 */
export function formatDeadline(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const day = dayKey.format(date)
  if (day === dayKey.format(now)) return `${clock(date)} today`
  if (day === dayKey.format(new Date(now.getTime() + 24 * 60 * 60 * 1000))) {
    return `${clock(date)} tomorrow`
  }
  return `${dayMonth.format(date)}, ${clock(date)}`
}

/** `reported_at + photo_edit_window_hours`, as an ISO string. */
export function addUntil(reportedAt: string, windowHours: number): string {
  return new Date(Date.parse(reportedAt) + windowHours * 60 * 60 * 1000).toISOString()
}
