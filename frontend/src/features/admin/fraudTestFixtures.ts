/** Test data for the fraud admin screens (used only by `*.test.tsx`). */

import type {
  AppealDetail,
  AppealRow,
  AuditItem,
  CaseDeed,
  CaseDetail,
  CasePage,
  CaseRow,
  FraudOptions,
} from './fraudApi'

export const PRIYA = { handle: 'priya', display_name: 'Priya Sharma' }
export const AHMED = { handle: 'ahmedk', display_name: 'Ahmed Khan' }
export const RAVI = { handle: 'ravi', display_name: 'Ravi Kumar' }
export const ADMIN = { handle: 'admin1', display_name: 'Anita Admin' }
export const OTHER_ADMIN = { handle: 'admin2', display_name: 'Bala Admin' }

const HOUR = 60 * 60 * 1000
export const hoursFromNow = (h: number) => new Date(Date.now() + h * HOUR).toISOString()

export function makeFraudOptions(overrides: Partial<FraudOptions> = {}): FraudOptions {
  return {
    types: [
      { id: 'reciprocal', label: 'Reciprocal', enabled: true, holds_on_open: false },
      { id: 'ring', label: 'Ring', enabled: true, holds_on_open: false },
      { id: 'concentration', label: 'Concentration', enabled: true, holds_on_open: false },
      { id: 'invite_anomaly', label: 'Invite anomaly', enabled: false, holds_on_open: false },
      { id: 'content', label: 'Content', enabled: true, holds_on_open: false },
    ],
    statuses: ['open', 'parked', 'resolved'],
    outcomes: ['confirmed', 'dismissed'],
    deed_outcomes: ['exclude', 'reject', 'release'],
    note_min_chars: 10,
    note_max_chars: 2000,
    override_reason_codes: [{ id: 'other', label: 'Other' }],
    override_max_points: 100,
    override_second_admin: { max_cut_points: 10, cut_to_zero: true, expire_days: 14 },
    explanation_reply_days: 14,
    appeal_window_days: 30,
    ...overrides,
  }
}

export function makeCaseRow(overrides: Partial<CaseRow> = {}): CaseRow {
  return {
    case_id: 'case-1',
    type: 'reciprocal',
    status: 'open',
    outcome: null,
    opened_at: hoursFromNow(-5),
    updated_at: hoursFromNow(-5),
    review_by: hoursFromNow(24 * 10),
    resolved_at: null,
    users: [AHMED, PRIYA],
    user_count: 2,
    deed_count: 3,
    held_count: 0,
    summary: 'Ahmed Khan and Priya Sharma recognised each other 3 times in 12 days',
    ...overrides,
  }
}

export function makeCasePage(items: CaseRow[], counts = { open: 2, parked: 1, resolved: 4 }) {
  const page: CasePage = { items, counts, next_cursor: null }
  return page
}

export function makeCaseDeed(overrides: Partial<CaseDeed> = {}): CaseDeed {
  return {
    deed_id: 'deed-1',
    reporter: PRIYA,
    doer: AHMED,
    category_label: 'Helping the elderly',
    area_name: 'Koramangala',
    description: 'Helped an old man cross the road',
    verified_at: '2026-11-10T06:00:00Z',
    status: 'verified',
    counts_for_points: true,
    held: false,
    outcome: null,
    independent_witness: false,
    ...overrides,
  }
}

type DetailOverrides = Partial<Omit<CaseDetail, 'case'>> & { case?: Partial<CaseDetail['case']> }

export function makeCaseDetail(overrides: DetailOverrides = {}): CaseDetail {
  const { case: header, ...rest } = overrides
  return {
    case: {
      ...makeCaseRow(),
      signal: { a_to_b: 2, b_to_a: 1, first_gap_hours: 20 },
      detector_config_version: 1,
      ...header,
    },
    users: [
      {
        user: AHMED,
        joined_at: '2026-10-01T05:00:00Z',
        verified_as_doer_in_window: 2,
        verified_as_reporter_in_window: 1,
        other_open_cases: 0,
        earlier_confirmed_cases: 0,
      },
      {
        user: PRIYA,
        joined_at: '2026-10-02T05:00:00Z',
        verified_as_doer_in_window: 1,
        verified_as_reporter_in_window: 2,
        other_open_cases: 1,
        earlier_confirmed_cases: 0,
      },
    ],
    deeds: [
      makeCaseDeed(),
      makeCaseDeed({
        deed_id: 'deed-2',
        reporter: AHMED,
        doer: PRIYA,
        category_label: 'Cleaning up',
        independent_witness: true,
      }),
    ],
    edges: [
      { from: PRIYA, to: AHMED, deeds: 2, shared_device: true, shared_network: false },
      { from: AHMED, to: PRIYA, deeds: 1, shared_device: false, shared_network: true },
    ],
    decisions: [],
    explanations: [],
    finalization: [{ period_month: '2026-11', finalizes_at: hoursFromNow(24 * 10), final: false }],
    window: { since: '2026-10-28T00:00:00Z', until: '2026-11-26T00:00:00Z' },
    ...rest,
  }
}

/** Both subjects asked; both replied (D99 is met). */
export function answeredExplanations(): CaseDetail['explanations'] {
  return [
    {
      user: AHMED,
      requested_at: hoursFromNow(-48),
      reply_due_at: hoursFromNow(24 * 12),
      reply: 'We work together at the shelter.',
      replied_at: hoursFromNow(-24),
    },
    {
      user: PRIYA,
      requested_at: hoursFromNow(-48),
      reply_due_at: hoursFromNow(-1),
      reply: null,
      replied_at: null,
    },
  ]
}

export function makeAppealRow(overrides: Partial<AppealRow> = {}): AppealRow {
  return {
    appeal_id: 'appeal-1',
    user: AHMED,
    kind: 'case',
    case_id: 'case-1',
    deed_count: 2,
    created_at: hoursFromNow(-3),
    original_decider: OTHER_ADMIN,
    outcome: null,
    decided_at: null,
    ...overrides,
  }
}

export function makeAppealDetail(overrides: Partial<AppealDetail> = {}): AppealDetail {
  return {
    appeal_id: 'appeal-1',
    user: AHMED,
    kind: 'case',
    case_id: 'case-1',
    override_id: null,
    reason: 'Priya and I volunteer together every weekend.',
    created_at: hoursFromNow(-3),
    original_decider: OTHER_ADMIN,
    notice: {
      notice_id: 'notice-1',
      created_at: hoursFromNow(-30),
      reason_key: 'reciprocal',
      reason_text:
        'These recognitions didn’t meet our rule that recognitions should come from independent witnesses',
      appeal_until: hoursFromNow(24 * 29),
      final_at: null,
      items: [
        {
          deed_id: 'deed-1',
          effect: 'excluded',
          old_points: null,
          new_points: null,
          reporter: PRIYA,
          doer: AHMED,
          category_label: 'Helping the elderly',
          area_name: 'Koramangala',
          occurred_on: '2026-11-10',
          status: 'verified',
        },
        {
          deed_id: 'deed-2',
          effect: 'pending_removal',
          old_points: null,
          new_points: null,
          reporter: AHMED,
          doer: PRIYA,
          category_label: 'Cleaning up',
          area_name: 'Indiranagar',
          occurred_on: '2026-11-12',
          status: 'verified',
        },
      ],
    },
    outcome: null,
    reviewer: null,
    reviewer_note: null,
    decided_at: null,
    ...overrides,
  }
}

export function makeAuditItem(overrides: Partial<AuditItem> = {}): AuditItem {
  return {
    id: 'audit-1',
    created_at: '2026-11-20T06:30:00Z',
    entity_type: 'flag_case',
    entity_id: 'case-1',
    action: 'flag_case.confirmed',
    actor: OTHER_ADMIN,
    reason: 'They admitted swapping recognitions.',
    old_value: { status: 'open' },
    new_value: { outcome: 'confirmed', exclude: 1 },
    ...overrides,
  }
}
