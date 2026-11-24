/** State and rules of the QR deed form (M2-DESIGN §5.3 Deed form). */

import type { DeedOptions, QrDeedRequest } from '../deeds/deedsApi'
import { descriptionError, PEOPLE_DEFAULT, toBeneficiariesCount } from '../deeds/formValues'

export type DeedFormValues = {
  /** A category slug, or null until one is chosen (required). */
  category: string | null
  description: string
  /** People helped; 0 means "not given". */
  people: number
  /** "Just now" leaves `occurred_at` to the server (the scan time). */
  when: 'now' | 'earlier'
  /** With "Earlier": how long before the scan, in minutes. */
  earlierMinutes: number
}

/** "How long before you scanned?" choices; those over `qr_occurred_max_age_minutes` are hidden. */
export const EARLIER_CHOICES = [
  { minutes: 15, label: 'About 15 minutes' },
  { minutes: 30, label: 'About 30 minutes' },
  { minutes: 60, label: 'About 1 hour' },
  { minutes: 120, label: 'About 2 hours' },
] as const

export const INITIAL_DEED_FORM: DeedFormValues = {
  category: null,
  description: '',
  people: PEOPLE_DEFAULT,
  when: 'now',
  earlierMinutes: EARLIER_CHOICES[0].minutes,
}

/** Interim copy (not on the canvas): Send without a category. */
export const CATEGORY_REQUIRED = 'Choose what kind of deed it was.'

/** The form's validation messages; empty when it can be sent. */
export function validateDeedForm(
  values: DeedFormValues,
  options: Pick<DeedOptions, 'description_min'>,
): { category?: string; description?: string } {
  const errors: { category?: string; description?: string } = {}
  if (!values.category) errors.category = CATEGORY_REQUIRED
  const description = descriptionError(values.description, options.description_min)
  if (description) errors.description = description
  return errors
}

/**
 * The `POST /api/deeds` body. "Just now" omits `occurred_at`; "Earlier" sends
 * `scanned_at − offset`; 0 people is sent as null.
 */
export function toQrDeedRequest(
  scan: { scan_id: string; scanned_at: string },
  values: DeedFormValues,
): QrDeedRequest {
  const request: QrDeedRequest = {
    method: 'qr_scan',
    scan_id: scan.scan_id,
    category: values.category ?? '',
    description: values.description.trim(),
    beneficiaries_count: toBeneficiariesCount(values.people),
  }
  if (values.when === 'earlier') {
    const scannedAt = Date.parse(scan.scanned_at)
    request.occurred_at = new Date(scannedAt - values.earlierMinutes * 60_000).toISOString()
  }
  return request
}
