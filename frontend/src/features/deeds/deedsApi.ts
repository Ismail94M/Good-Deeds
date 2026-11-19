/** Types and calls for the M2 deeds API (docs/M2-DESIGN.md §3; backend/app/modules/deeds). */

import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from '../../lib/api'
import type { Location } from '../../lib/geolocation'

export type { Location } from '../../lib/geolocation'

// ----------------------------------------------------------------- shared

/** `{handle, display_name}` of another user, as embedded in deed, scan and invite responses. */
export type UserSummary = {
  handle: string
  display_name: string
}

export type Page<T> = {
  items: T[]
  next_cursor: string | null
}

export type DeedStatus =
  'pending' | 'verified' | 'unverified' | 'rejected' | 'removed' | 'withdrawn'

/** The intended verification path, set at submit. */
export type VerificationMethod = 'qr_scan' | 'invite_accept'

export type InviteStatus = 'pending' | 'accepted' | 'expired' | 'cancelled'

/** How the reporter says they shared the invite link (client-reported). */
export type InviteSentVia = 'whatsapp' | 'share_sheet' | 'copy_link' | 'other'

export type Category = {
  slug: string
  label: string
}

// ----------------------------------------------------------------- scans

/** `POST /scans` body: exactly one identity form (QR token, or ID + 6-digit code), plus location. */
export type ScanRequest =
  { token: string; location: Location } | { short_id: string; code: string; location: Location }

export type ScanOut = {
  scan_id: string
  doer: UserSummary
  expires_at: string
  /** The server's scan time. The deed form's "Earlier" choices are offsets from it. */
  scanned_at: string
  /**
   * Reports this witness may still make about this doer this IST month, counted before this
   * report (so 1 means "this is the last one"). Recomputed on every read (D20).
   */
  pair_reports_remaining: number
  /** 00:00 IST on the 1st of next month, with the `+05:30` offset. Never compute it locally. */
  month_resets_at: string
  /** The scan location's area (ward name), or "Bangalore" (M3-DESIGN §3.1). Never coordinates. */
  area_name: string
}

/** `GET /scans/{id}`: the POST shape plus whether a deed has already used the scan. */
export type ScanDetail = ScanOut & {
  consumed: boolean
}

// ----------------------------------------------------------------- deeds

export type QrDeedRequest = {
  method: 'qr_scan'
  scan_id: string
  /** A category `slug` from `/deeds/options`. */
  category: string
  description: string
  beneficiaries_count?: number | null
  /** Defaults to the scan time on the server. */
  occurred_at?: string
}

export type InviteDeedRequest = {
  method: 'invite'
  category: string
  description: string
  beneficiaries_count?: number | null
  occurred_at: string
  /** Optional: only when the reporter is still at the place (D5). */
  location?: Location | null
  sent_via: InviteSentVia
}

export type CreateDeedRequest = QrDeedRequest | InviteDeedRequest

export type InviteOut = {
  id: string
  deed_id: string
  status: InviteStatus
  sent_via: InviteSentVia
  created_at: string
  expires_at: string
  accepted_at: string | null
  /** The raw invite link. Present only on create and reissue; it is never shown again. */
  url?: string
}

export type SameActOut = {
  report_count: number
  counts_for_points: boolean
}

export type DeedOut = {
  id: string
  status: DeedStatus
  verification_method: VerificationMethod
  category: string
  description: string
  beneficiaries_count: number | null
  occurred_at: string
  reported_at: string
  verified_at: string | null
  withdrawn_at: string | null
  last_edited_at: string | null
  reporter: UserSummary
  /** Always null while the deed is pending (PRD §6.5): never show a doer for pending deeds. */
  doer: UserSummary | null
  same_act: SameActOut | null
  prize_rank_eligible: boolean
  has_location: boolean
  /** The deed's area (ward name), or "Bangalore" (M3-DESIGN §3.1). Never coordinates. */
  area_name: string
  /** True only for the reporter, while pending or verified. */
  can_edit: boolean
  can_withdraw: boolean
  /** Reporter only. */
  invite: InviteOut | null
}

/** `PATCH /deeds/{id}`: only these fields are editable (D7). */
export type DeedUpdate = {
  category?: string
  description?: string
  beneficiaries_count?: number | null
}

export type MyDeedsRole = 'doer' | 'reporter'

export type MyDeedsParams = {
  role: MyDeedsRole
  cursor?: string | null
  limit?: number
}

/** Filter for `GET /me/invites` (the contract lists these three; `cancelled` is not a filter). */
export type InviteStatusFilter = 'pending' | 'accepted' | 'expired'

export type MyInvitesParams = {
  status?: InviteStatusFilter
  cursor?: string | null
}

/** `GET /deeds/options` (§3.2 #10). The limits are returned so copy follows config. */
export type DeedOptions = {
  categories: Category[]
  invites_enabled: boolean
  invites_remaining_today: number
  invite_expiry_hours: number
  /** `max(0, reporter_monthly_limit − deeds created this IST month)`. */
  reports_remaining_this_month: number
  /** Total reports per reporter per IST month (150; D19). */
  reporter_monthly_limit: number
  /** Reports about one doer per reporter per IST month (15; D19). */
  reporter_doer_monthly_limit: number
  /** 00:00 IST on the 1st of next month, with the `+05:30` offset. */
  month_resets_at: string
  description_min: number
  description_max: number
  beneficiaries_max: number
  scan_submit_minutes: number
  qr_occurred_max_age_minutes: number
  max_location_accuracy_m: number
  service_area_radius_km: number
  /**
   * Reports per act that count (D2/D15). Not sent by the M2 API yet; the Q20 note falls back
   * to `DEFAULT_SAME_ACT_CAP` until it is.
   */
  same_act_cap?: number
}

/** The same-act cap the F6 "Extra witness" note uses when options don't carry it (Q20). */
export const DEFAULT_SAME_ACT_CAP = 5

// ----------------------------------------------------------------- invites (public preview)

export type InvitePreview = {
  status: 'pending'
  reporter: UserSummary
  category: string
  description: string
  occurred_at: string
  expires_at: string
}

// ----------------------------------------------------------------- self-logs

export type SelfLogCreate = {
  category: string
  description: string
  occurred_at: string
  location?: Location
}

/** Any subset of the create fields; `location: null` removes the stored location. */
export type SelfLogUpdate = {
  category?: string
  description?: string
  occurred_at?: string
  location?: Location | null
}

export type SelfLogOut = {
  id: string
  category: string
  description: string
  occurred_at: string
  has_location: boolean
  created_at: string
  updated_at: string
}

// ----------------------------------------------------------------- profile counts

export type DeedCountsOut = {
  self_logged: number
  verified: number
}

// ----------------------------------------------------------------- errors

/** Error codes the deeds endpoints return (§3.2), plus the generic ones the F3 screens handle. */
export type DeedsErrorCode =
  | 'not_authenticated'
  | 'onboarding_required'
  | 'rate_limited'
  // scans
  | 'qr_invalid'
  | 'doer_code_invalid'
  | 'cannot_verify_self'
  | 'already_scanned'
  | 'already_recognized_today'
  | 'location_too_imprecise'
  | 'outside_service_area'
  | 'implausible_travel'
  | 'monthly_report_limit'
  | 'pair_monthly_limit'
  // deed create, scan read
  | 'scan_not_found'
  | 'scan_expired'
  | 'scan_used'
  | 'invalid_category'
  | 'invalid_description'
  | 'invalid_beneficiaries'
  | 'implausible_time'
  | 'invite_daily_limit'
  | 'invites_disabled'
  // reads, edit, withdraw
  | 'deed_not_found'
  | 'deed_not_editable'
  | 'deed_not_withdrawable'

/**
 * The doer named in a scan-time error body (D27): only 409 `already_recognized_today` and
 * 429 `pair_monthly_limit` from `POST /scans` carry `doer: {handle, display_name}`. Returns null
 * for any other error or a malformed body.
 */
export function errorDoer(error: unknown): UserSummary | null {
  if (!(error instanceof ApiError)) return null
  const doer = error.extra.doer
  if (!doer || typeof doer !== 'object') return null
  const { handle, display_name } = doer as Record<string, unknown>
  if (typeof handle !== 'string' || typeof display_name !== 'string') return null
  return { handle, display_name }
}

// ----------------------------------------------------------------- calls

/** Build `?a=1&b=2` from defined, non-empty values (or '' when there are none). */
function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const seg = encodeURIComponent

export const deedsApi = {
  // scans
  createScan: (data: ScanRequest) => apiPost<ScanOut>('/scans', data),
  scan: (scanId: string) => apiGet<ScanDetail>(`/scans/${seg(scanId)}`),

  // deeds
  createDeed: (data: CreateDeedRequest) => apiPost<DeedOut>('/deeds', data),
  deed: (id: string) => apiGet<DeedOut>(`/deeds/${seg(id)}`),
  updateDeed: (id: string, data: DeedUpdate) => apiPatch<DeedOut>(`/deeds/${seg(id)}`, data),
  withdrawDeed: (id: string) => apiPost<DeedOut>(`/deeds/${seg(id)}/withdraw`),
  options: () => apiGet<DeedOptions>('/deeds/options'),
  myDeeds: ({ role, cursor, limit }: MyDeedsParams) =>
    apiGet<Page<DeedOut>>(`/me/deeds${query({ role, cursor, limit })}`),

  // invites
  myInvites: ({ status, cursor }: MyInvitesParams = {}) =>
    apiGet<Page<InviteOut>>(`/me/invites${query({ status, cursor })}`),
  reissueInvite: (inviteId: string) => apiPost<InviteOut>(`/invites/${seg(inviteId)}/reissue`),
  invitePreview: (token: string) => apiGet<InvitePreview>(`/invites/by-token/${seg(token)}`),
  acceptInvite: (token: string) => apiPost<DeedOut>(`/invites/by-token/${seg(token)}/accept`),

  // self-logs
  createSelfLog: (data: SelfLogCreate) => apiPost<SelfLogOut>('/me/self-logs', data),
  selfLogs: (cursor?: string | null) =>
    apiGet<Page<SelfLogOut>>(`/me/self-logs${query({ cursor })}`),
  updateSelfLog: (id: string, data: SelfLogUpdate) =>
    apiPatch<SelfLogOut>(`/me/self-logs/${seg(id)}`, data),
  deleteSelfLog: (id: string) => apiDelete(`/me/self-logs/${seg(id)}`),

  // profile counts (public)
  deedCounts: (handle: string) => apiGet<DeedCountsOut>(`/users/${seg(handle)}/deed-counts`),
}

/** TanStack Query keys, all under `['deeds', …]` (M2-DESIGN §5.3). */
export const deedsKeys = {
  all: ['deeds'] as const,
  options: ['deeds', 'options'] as const,
  scan: (scanId: string) => ['deeds', 'scan', scanId] as const,
  deed: (id: string) => ['deeds', 'deed', id] as const,
  myDeeds: (role: MyDeedsRole) => ['deeds', 'mine', role] as const,
  myInvites: (status?: InviteStatusFilter) => ['deeds', 'invites', status ?? 'all'] as const,
  invitePreview: (token: string) => ['deeds', 'invite-preview', token] as const,
  selfLogs: ['deeds', 'self-logs'] as const,
  counts: (handle: string) => ['deeds', 'counts', handle.toLowerCase()] as const,
}
