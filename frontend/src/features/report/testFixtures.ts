/** Test data for the Record a deed screens (used only by `*.test.tsx`). */

import type { DeedOptions, DeedOut, ScanDetail, ScanOut } from '../deeds/deedsApi'

export const AHMED = { handle: 'ahmedk', display_name: 'Ahmed Khan' }

export function makeOptions(overrides: Partial<DeedOptions> = {}): DeedOptions {
  return {
    categories: [
      { slug: 'helping_elderly', label: 'Helping an elderly person' },
      { slug: 'animals', label: 'Animals' },
      { slug: 'other', label: 'Other' },
    ],
    invites_enabled: false,
    invites_remaining_today: 3,
    invite_expiry_hours: 168,
    reports_remaining_this_month: 140,
    reporter_monthly_limit: 150,
    reporter_doer_monthly_limit: 15,
    month_resets_at: '2026-12-01T00:00:00+05:30',
    description_min: 10,
    description_max: 1000,
    beneficiaries_max: 10000,
    scan_submit_minutes: 15,
    qr_occurred_max_age_minutes: 120,
    max_location_accuracy_m: 500,
    service_area_radius_km: 40,
    ...overrides,
  }
}

export const SCAN_ID = '11111111-2222-3333-4444-555555555555'

export function makeScan(overrides: Partial<ScanOut> = {}): ScanOut {
  const now = Date.now()
  return {
    scan_id: SCAN_ID,
    doer: AHMED,
    scanned_at: new Date(now).toISOString(),
    expires_at: new Date(now + 15 * 60_000).toISOString(),
    pair_reports_remaining: 12,
    month_resets_at: '2026-12-01T00:00:00+05:30',
    area_name: 'Koramangala',
    ...overrides,
  }
}

export function makeScanDetail(overrides: Partial<ScanDetail> = {}): ScanDetail {
  return { ...makeScan(overrides), consumed: false, ...overrides }
}

export function makeDeed(overrides: Partial<DeedOut> = {}): DeedOut {
  return {
    id: 'deed-1',
    status: 'verified',
    verification_method: 'qr_scan',
    category: 'helping_elderly',
    description: 'Helped an elderly man cross the road.',
    beneficiaries_count: 1,
    occurred_at: new Date().toISOString(),
    reported_at: new Date().toISOString(),
    verified_at: new Date().toISOString(),
    withdrawn_at: null,
    last_edited_at: null,
    reporter: { handle: 'asha', display_name: 'Asha' },
    doer: AHMED,
    same_act: { report_count: 1, counts_for_points: true },
    prize_rank_eligible: true,
    has_location: true,
    area_name: 'Koramangala',
    can_edit: true,
    can_withdraw: true,
    invite: null,
    ...overrides,
  }
}

/** A good fix in central Bangalore. */
export const GOOD_FIX = { lat: 12.9716, lng: 77.5946, accuracy_m: 35 }
