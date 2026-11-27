/**
 * Types and calls for the M5 scoring admin screens (docs/M5-DESIGN.md §3.4 #9, #13–#18;
 * backend/app/modules/scoring/schemas.py): a deed's points with its hold and overrides, the
 * override form (200 applied, 202 waiting for a second admin, Q77), the override-request queue,
 * a person's titles and badges, and revocation (D92). Admin only.
 *
 * The revoke dialog also reads two fraud endpoints (#1 options, #2 cases filtered by person); their
 * shapes here are only the fields these screens use, so this file stays independent of
 * `fraudApi.ts`.
 */

import { apiGet, apiPost, queryString } from '../../lib/api'
import type { AdminDeedScoring as M4AdminDeedScoring, RegionKind } from '../scoring/scoringApi'
import { scoringKeys } from '../scoring/scoringApi'
import type { SecondAdminRule } from './fraudApi'

const seg = encodeURIComponent

export type ScoringRole = 'doer' | 'reporter'

/** `UserSummary`: `handle` is null for an account that is no longer active. */
export type AdminUserRef = { handle: string | null; display_name: string }

/** One `score_overrides` row. `points` null: the override was cleared (back to the rules). */
export type ScoreOverride = {
  id: string
  role: ScoringRole
  points: number | null
  previous_points: number
  reason_code: string
  note: string
  actor: AdminUserRef | null
  case_id: string | null
  request_id: string | null
  created_at: string
}

/**
 * M4 #9 plus M5 `held` (a hold from a confirmed decision), the overrides, oldest first, and the
 * deed's month: `period_month` (`YYYY-MM`, the IST month of its ledger rows; null with none) and
 * `month_final` (that month is finalized).
 */
export type AdminDeedScoring = M4AdminDeedScoring & {
  held: boolean
  period_month: string | null
  month_final: boolean
  overrides: ScoreOverride[]
}

export type OverrideBody = {
  role: ScoringRole
  /** null: back to the rules (clears the override in force). */
  points: number | null
  reason_code: string
  note: string
  case_id?: string | null
}

export type OverrideRequestStatus = 'pending' | 'approved' | 'declined' | 'expired'

/** #13 (202) and #17: a big cut waiting for a second admin. */
export type OverrideRequest = {
  id: string
  deed_id: string
  role: ScoringRole
  user: AdminUserRef | null
  previous_points: number
  points: number
  reason_code: string
  note: string
  case_id: string | null
  requested_by: AdminUserRef | null
  created_at: string
  expires_at: string
  status: OverrideRequestStatus
  decided_by: AdminUserRef | null
  decision_note: string | null
  decided_at: string | null
  override_id: string | null
}

export type OverrideRequestTab = 'pending' | 'decided'

export type OverrideRequestPage = {
  items: OverrideRequest[]
  counts: { pending: number; decided: number }
  next_cursor: string | null
}

/** #13 result: applied now (200), or stored as a request for a second admin (202). */
export type OverrideResult =
  { kind: 'applied'; deed: AdminDeedScoring } | { kind: 'requested'; request: OverrideRequest }

export type OverrideDecision = 'approve' | 'decline'

export type OverrideDecisionResult = { request: OverrideRequest; deed: AdminDeedScoring | null }

export type AdminTitle = {
  id: string
  label: string
  period_month: string
  region_name: string
  level: RegionKind
  place: number
  revoked_at: string | null
  revoked_reason: string | null
}

export type AdminBadge = {
  id: string
  slug: string
  label: string
  awarded_at: string
  revoked_at: string | null
  revoked_reason: string | null
}

/** #14. */
export type AdminUserScoring = {
  user: AdminUserRef
  titles: AdminTitle[]
  badges: AdminBadge[]
  doer_points: number
  reporter_points: number
}

/** The fields of `GET /admin/fraud/options` (#1) that these screens use. */
export type FraudOptionsForScoring = {
  note_min_chars: number
  note_max_chars: number
  override_reason_codes: { id: string; label: string }[]
  override_max_points: number
  override_second_admin: SecondAdminRule
}

/** The fields of a `CaseRow` (#2) that the revoke dialog uses. */
export type CaseChoice = {
  case_id: string
  type: string
  status: string
  outcome: string | null
  opened_at: string
  resolved_at: string | null
  summary: string
}

type CasePage = { items: CaseChoice[]; next_cursor: string | null }

/**
 * `POST` #13. The 202 body is the pending request; everything else is the #9 body. The API
 * client only exposes the parsed body, so the two are told apart by shape: only a request
 * carries `requested_by`.
 */
async function override(deedId: string, body: OverrideBody): Promise<OverrideResult> {
  const result = await apiPost<AdminDeedScoring | OverrideRequest>(
    `/admin/scoring/deeds/${seg(deedId)}/overrides`,
    body,
  )
  if ('requested_by' in result) return { kind: 'requested', request: result }
  return { kind: 'applied', deed: result }
}

export const scoringAdminApi = {
  deed: (deedId: string) => apiGet<AdminDeedScoring>(`/admin/scoring/deeds/${seg(deedId)}`),
  override,
  requests: (status: OverrideRequestTab, cursor: string | null) =>
    apiGet<OverrideRequestPage>(
      `/admin/scoring/override-requests${queryString({ status, cursor })}`,
    ),
  decide: (requestId: string, decision: OverrideDecision, note: string) =>
    apiPost<OverrideDecisionResult>(`/admin/scoring/override-requests/${seg(requestId)}/decision`, {
      decision,
      note,
    }),
  user: (handle: string) => apiGet<AdminUserScoring>(`/admin/scoring/users/${seg(handle)}`),
  revokeTitle: (titleId: string, caseId: string, note: string) =>
    apiPost<AdminUserScoring>(`/admin/scoring/titles/${seg(titleId)}/revoke`, {
      case_id: caseId,
      note,
    }),
  revokeBadge: (badgeId: string, caseId: string, note: string) =>
    apiPost<AdminUserScoring>(`/admin/scoring/badges/${seg(badgeId)}/revoke`, {
      case_id: caseId,
      note,
    }),
  fraudOptions: () => apiGet<FraudOptionsForScoring>('/admin/fraud/options'),
  resolvedCases: (handle: string) =>
    apiGet<CasePage>(`/admin/fraud/cases${queryString({ status: 'resolved', user: handle })}`),
}

export const scoringAdminKeys = {
  deed: scoringKeys.adminDeed,
  requests: (status: OverrideRequestTab) =>
    ['admin', 'scoring', 'override-requests', status] as const,
  allRequests: ['admin', 'scoring', 'override-requests'] as const,
  user: (handle: string) => ['admin', 'scoring', 'user', handle.toLowerCase()] as const,
  // Own keys (not the fraud screens' ones), so a different cache shape there can't clash.
  fraudOptions: ['admin', 'scoring', 'fraud-options'] as const,
  resolvedCases: (handle: string) =>
    ['admin', 'scoring', 'user-cases', handle.toLowerCase()] as const,
}
