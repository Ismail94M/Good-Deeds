/**
 * Types and calls for the M5 fraud admin API (docs/M5-DESIGN.md §3.2, §3.3 #1–#8, #10–#12).
 * Admin only: members and moderators get 403, and a conflicted admin gets 404 on a case or an
 * appeal (D90, D100). The shapes follow `backend/app/modules/fraud/schemas.py`.
 */

import { useQuery } from '@tanstack/react-query'
import { apiGet, apiPost, queryString } from '../../lib/api'

const seg = encodeURIComponent

export type UserRef = { handle: string; display_name: string }

// ----------------------------------------------------------------- #1 options

export type CaseType = 'reciprocal' | 'ring' | 'concentration' | 'invite_anomaly' | 'content'
export type CaseQueueStatus = 'open' | 'parked' | 'resolved'
export type CaseOutcome = 'confirmed' | 'dismissed'
export type DeedAction = 'exclude' | 'reject' | 'release'

export type CaseTypeOption = {
  id: string
  label: string
  enabled: boolean
  holds_on_open: boolean
}

/** Q77: overrides that need a second admin (to 0 when `cut_to_zero`, or a cut of more than
 * `max_cut_points`); requests expire after `expire_days`. */
export type SecondAdminRule = {
  max_cut_points: number
  cut_to_zero: boolean
  expire_days: number
}

export type FraudOptions = {
  types: CaseTypeOption[]
  statuses: string[]
  outcomes: string[]
  deed_outcomes: string[]
  note_min_chars: number
  note_max_chars: number
  override_reason_codes: { id: string; label: string }[]
  override_max_points: number
  override_second_admin: SecondAdminRule
  explanation_reply_days: number
  appeal_window_days: number
}

// ----------------------------------------------------------------- #2 queue, #3 detail

export type CaseRow = {
  case_id: string
  type: string
  status: CaseQueueStatus
  outcome: CaseOutcome | null
  opened_at: string
  updated_at: string
  review_by: string | null
  resolved_at: string | null
  /** The first 5 subjects; `user_count` is all of them. */
  users: UserRef[]
  user_count: number
  deed_count: number
  held_count: number
  /** Server-made, e.g. "Priya and Ahmed recognised each other 3 times in 12 days". */
  summary: string
}

export type CasePage = {
  items: CaseRow[]
  counts: { open: number; parked: number; resolved: number }
  next_cursor: string | null
}

export type CaseHeader = CaseRow & {
  signal: Record<string, unknown>
  detector_config_version: number
}

export type CaseDeed = {
  deed_id: string
  reporter: UserRef
  doer: UserRef
  category_label: string
  area_name: string
  description: string
  verified_at: string | null
  status: string
  counts_for_points: boolean
  held: boolean
  outcome: DeedAction | null
  /** Another verified report of the same act came from someone outside the case (D88). */
  independent_witness: boolean
}

export type CaseEdge = {
  from: UserRef
  to: UserRef
  deeds: number
  shared_device: boolean
  shared_network: boolean
}

export type CaseUser = {
  user: UserRef
  joined_at: string
  verified_as_doer_in_window: number
  verified_as_reporter_in_window: number
  other_open_cases: number
  earlier_confirmed_cases: number
}

export type CaseDecisionRecord = {
  action: 'park' | 'unpark' | 'request_explanation' | 'confirm' | 'dismiss' | string
  note: string
  actor: UserRef
  created_at: string
}

export type CaseExplanation = {
  user: UserRef
  requested_at: string
  reply_due_at: string
  reply: string | null
  replied_at: string | null
}

export type FinalizationMonth = { period_month: string; finalizes_at: string; final: boolean }

export type CaseDetail = {
  case: CaseHeader
  users: CaseUser[]
  deeds: CaseDeed[]
  edges: CaseEdge[]
  decisions: CaseDecisionRecord[]
  explanations: CaseExplanation[]
  finalization: FinalizationMonth[]
  /** The period the per-user counts cover. */
  window: { since: string; until: string }
}

export type CasesParams = {
  status: CaseQueueStatus
  type?: string | null
  /** Filter by subject handle (used by revocation, F23). */
  user?: string | null
  cursor?: string | null
}

export type NoteRequest = { note: string }

export type DecisionRequest = {
  outcome: CaseOutcome
  note: string
  deeds?: { deed_id: string; action: DeedAction }[]
}

export type ExplanationsRequest = { handles?: string[]; note: string }

// ----------------------------------------------------------------- #7 status

export type FraudStatus = {
  config_version: number
  detectors: {
    detector: string
    enabled: boolean
    last_run: {
      status: string
      started_at: string
      finished_at: string | null
      edges_scanned: number
      cases_opened: number
    } | null
    /** Per-deed checks that hit a search limit in the last 7 days (SEC5-M6); the nightly sweep retries them. */
    truncated_checks_7d: number
  }[]
  open_cases_by_type: Record<string, number>
  /** Open appeals no active admin may decide: they wait (PO Q78). */
  appeals_waiting_for_second_admin: number
}

// ----------------------------------------------------------------- #8 audit

export type AuditItem = {
  id: string
  created_at: string
  entity_type: string
  entity_id: string
  action: string
  actor: UserRef | null
  reason: string | null
  old_value: Record<string, unknown> | null
  new_value: Record<string, unknown> | null
}

export type AuditPage = { items: AuditItem[]; next_cursor: string | null }

/** The filters as the API takes them (`from` / `to` are ISO date-times). */
export type AuditFilters = {
  entity_type?: string
  entity_id?: string
  actor?: string
  action?: string
  from?: string
  to?: string
}

// ----------------------------------------------------------------- #11, #12 appeals

export type AppealQueueStatus = 'open' | 'decided'
export type AppealOutcome = 'upheld' | 'overturned'

export type AppealRow = {
  appeal_id: string
  user: UserRef
  kind: 'case' | 'override'
  case_id: string | null
  deed_count: number
  created_at: string
  original_decider: UserRef
  outcome: AppealOutcome | null
  decided_at: string | null
}

export type AppealPage = {
  items: AppealRow[]
  counts: { open: number; decided: number }
  next_cursor: string | null
}

export type AppealNoticeItem = {
  deed_id: string
  /** `excluded`, `pending_removal` or `points_changed`. */
  effect: string
  old_points: number | null
  new_points: number | null
  reporter: UserRef
  doer: UserRef
  category_label: string
  area_name: string
  occurred_on: string | null
  status: string | null
}

export type AppealDetail = {
  appeal_id: string
  user: UserRef
  kind: 'case' | 'override'
  case_id: string | null
  override_id: string | null
  /** The person's written reason. */
  reason: string
  created_at: string
  original_decider: UserRef
  notice: {
    notice_id: string
    created_at: string
    reason_key: string
    /** What the person was told (D101). */
    reason_text: string
    appeal_until: string
    final_at: string | null
    items: AppealNoticeItem[]
  }
  outcome: AppealOutcome | null
  reviewer: UserRef | null
  reviewer_note: string | null
  decided_at: string | null
}

export type AppealDecisionRequest = { outcome: AppealOutcome; note: string }

// ----------------------------------------------------------------- calls

export const fraudApi = {
  options: () => apiGet<FraudOptions>('/admin/fraud/options'),
  status: () => apiGet<FraudStatus>('/admin/fraud/status'),
  cases: ({ status, type, user, cursor }: CasesParams) =>
    apiGet<CasePage>(`/admin/fraud/cases${queryString({ status, type, user, cursor })}`),
  case: (caseId: string) => apiGet<CaseDetail>(`/admin/fraud/cases/${seg(caseId)}`),
  park: (caseId: string, data: NoteRequest) =>
    apiPost<CaseDetail>(`/admin/fraud/cases/${seg(caseId)}/park`, data),
  unpark: (caseId: string, data: NoteRequest) =>
    apiPost<CaseDetail>(`/admin/fraud/cases/${seg(caseId)}/unpark`, data),
  decide: (caseId: string, data: DecisionRequest) =>
    apiPost<CaseDetail>(`/admin/fraud/cases/${seg(caseId)}/decision`, data),
  askForExplanations: (caseId: string, data: ExplanationsRequest) =>
    apiPost<CaseDetail>(`/admin/fraud/cases/${seg(caseId)}/explanations`, data),
  audit: (filters: AuditFilters, cursor: string | null) =>
    apiGet<AuditPage>(`/admin/audit${queryString({ ...filters, cursor })}`),
  appeals: (status: AppealQueueStatus, cursor: string | null) =>
    apiGet<AppealPage>(`/admin/fraud/appeals${queryString({ status, cursor })}`),
  appeal: (appealId: string) => apiGet<AppealDetail>(`/admin/fraud/appeals/${seg(appealId)}`),
  decideAppeal: (appealId: string, data: AppealDecisionRequest) =>
    apiPost<AppealDetail>(`/admin/fraud/appeals/${seg(appealId)}/decision`, data),
}

/** TanStack Query keys (§6.3), all under `['admin', …]`. */
export const fraudKeys = {
  all: ['admin', 'fraud'] as const,
  options: ['admin', 'fraud', 'options'] as const,
  status: ['admin', 'fraud', 'status'] as const,
  cases: (status: CaseQueueStatus, type: string | null = null, user: string | null = null) =>
    ['admin', 'fraud', status, type, user] as const,
  case: (caseId: string) => ['admin', 'fraud', 'case', caseId] as const,
  appeals: (status: AppealQueueStatus) => ['admin', 'fraud', 'appeals', status] as const,
  appeal: (appealId: string) => ['admin', 'fraud', 'appeal', appealId] as const,
  audit: (filters: AuditFilters) => ['admin', 'audit', filters] as const,
}

/** Labels and limits for every fraud screen (#1); they change rarely. */
export function useFraudOptions() {
  return useQuery({
    queryKey: fraudKeys.options,
    queryFn: fraudApi.options,
    staleTime: 5 * 60_000,
  })
}

/** The type label from the options, or a readable fallback while they load. */
export function caseTypeLabel(options: FraudOptions | undefined, type: string): string {
  const label = options?.types.find((t) => t.id === type)?.label
  if (label) return label
  const text = type.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
