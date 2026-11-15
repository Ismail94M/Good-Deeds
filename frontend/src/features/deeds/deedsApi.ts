/** Types and calls for the M2 deeds API (docs/M2-DESIGN.md §3; backend/app/modules/deeds). */

import { apiDelete, apiGet, apiPatch, apiPost } from '../../lib/api'
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

export type DeedOptions = {
  categories: Category[]
  invites_enabled: boolean
  invites_remaining_today: number
  reports_remaining_this_month: number
  invite_expiry_hours: number
  description_min: number
  description_max: number
  scan_submit_minutes: number
}

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
