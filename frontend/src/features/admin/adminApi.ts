/**
 * Types and calls for the M3 admin review API: photo reports (M3-DESIGN §3.3 #11–#14) and
 * comment reports (§3.4a #14–#17). Moderators and admins only (Q34); anyone else gets 403
 * `forbidden`. A reviewer who is part of the deed gets 403 `conflict_of_interest` on writes.
 */

import { apiGet, apiPost, queryString } from '../../lib/api'
import type { Page, UserSummary } from '../deeds/deedsApi'
import type { CommentReportReason } from '../feed/feedApi'
import type { PhotoReportReason, ReportPriority } from '../media/mediaApi'

export type { ReportPriority } from '../media/mediaApi'

const seg = encodeURIComponent

type UserRef = UserSummary

export type ReportQueueStatus = 'open' | 'resolved'

/** `closed`: the photo or comment was deleted by its owner while the case was open. */
export type CaseStatus = 'open' | 'resolved' | 'closed'
export type CaseDecision = 'kept' | 'removed'
export type DecisionAction = 'keep' | 'remove'

/** A queue page plus the counts for the "Open · {n}" / "Resolved · {n}" tab labels. */
export type ReportQueue<T> = Page<T> & {
  open_count: number
  resolved_count: number
}

export type ReportQueueParams = {
  status: ReportQueueStatus
  cursor?: string | null
  /** Photo reports only (M6): `deed` or `avatar`; omit for both. */
  purpose?: 'deed' | 'avatar' | null
}

/** One entry of a case's decision history (keep, remove, restore). */
export type DecisionRecord = {
  action: DecisionAction | 'restore'
  note: string
  actor: UserRef
  created_at: string
}

// ----------------------------------------------------------------- photo reports

/** M6 D121: deed photos and profile pictures share one queue. */
export type PhotoPurpose = 'deed' | 'avatar'

export type PhotoReportItem = {
  case_id: string
  /** Absent from a pre-M6 API: a deed photo. */
  purpose?: PhotoPurpose
  priority: ReportPriority
  top_reason: PhotoReportReason
  report_count: number
  opened_at: string
  oldest_report_at: string
  photo_thumb_url: string | null
  /** Deed photos only (null for a profile picture). */
  deed: { doer: UserRef; category: string; area_name: string } | null
  /** Profile pictures only: whose picture it is. */
  owner?: UserRef | null
}

/**
 * The `case` object of the review page. §3.3 #12 names it without listing its fields; these are
 * the `photo_report_cases` columns a reviewer needs (§2.2), minus internal IDs.
 */
export type PhotoReportCase = {
  case_id: string
  status: CaseStatus
  priority: ReportPriority
  top_reason: PhotoReportReason
  report_count: number
  opened_at: string
  resolved_at: string | null
  decision: CaseDecision | null
  removal_reason: PhotoReportReason | null
}

export type CaseReporter = { kind: 'member'; handle: string } | { kind: 'visitor' }

export type PhotoReportEntry = {
  reason: PhotoReportReason
  details: string | null
  reporter: CaseReporter
  created_at: string
}

export type PhotoReportDetail = {
  case: PhotoReportCase
  purpose?: PhotoPurpose
  /** Profile pictures only: the owner, shown instead of a deed (M6 §3.3). */
  owner?: UserRef | null
  photo: {
    /** Needed for `POST /admin/photos/{photo_id}/restore` (§3.4b 4). */
    id: string
    /** Admin-only signed URL; works after removal too (held copy). */
    display_url: string
    position: number
    photo_count: number
    uploaded_at: string
    uploader: UserRef
    blurred_count: number
    state: 'visible' | 'removed'
    /** `removed_at + removed_photo_retention_days` while admin-removed and not purged. */
    restorable_until: string | null
    /** Set once a removed photo's objects are deleted for good ("Deleted for good on…"). */
    purged_at: string | null
  }
  /** Null for a profile picture. */
  deed: {
    doer: UserRef
    reporter: UserRef
    category: string
    area_name: string
    status: string
    listed: boolean
  } | null
  reports: PhotoReportEntry[]
  decisions: DecisionRecord[]
}

export type PhotoDecisionRequest = {
  action: DecisionAction
  /** Required, 5..`decision_note_max` characters. */
  note: string
  /** For `remove`: the reason shown to the uploader (defaults to the top report's reason). */
  removal_reason?: PhotoReportReason
}

// ----------------------------------------------------------------- comment reports

export type CommentReportItem = {
  case_id: string
  priority: ReportPriority
  top_reason: CommentReportReason
  report_count: number
  opened_at: string
  oldest_report_at: string
  /** First 80 characters of the comment. */
  comment_excerpt: string
  author: UserRef
  deed: { doer: UserRef; category: string; area_name: string }
}

export type CommentReportCase = {
  case_id: string
  status: CaseStatus
  priority: ReportPriority
  top_reason: CommentReportReason
  report_count: number
  opened_at: string
  resolved_at: string | null
  decision: CaseDecision | null
}

export type CommentReportEntry = {
  reason: CommentReportReason
  details: string | null
  /** Comment reports come from members only. */
  reporter: { kind: 'member'; handle: string }
  created_at: string
}

/** `GET /admin/comment-reports/{case_id}` (§3.4a #15, shape settled in §3.4b 5). */
export type CommentReportDetail = {
  case: CommentReportCase
  comment: {
    /** Needed for `POST /admin/comments/{comment_id}/restore`. */
    id: string
    /** Kept for `deleted_comment_retention_days` after a removal; null once erased. */
    body: string | null
    author: UserRef
    created_at: string
    /** `removed` by moderation; `deleted` by its author, the doer or the reporter. */
    state: 'visible' | 'removed' | 'deleted'
    /** Until when a moderation removal can be restored; null otherwise. */
    restorable_until: string | null
  }
  deed: {
    doer: UserRef
    reporter: UserRef
    category: string
    area_name: string
    listed: boolean
  }
  reports: CommentReportEntry[]
  decisions: DecisionRecord[]
  /** Comments by this author removed by moderation in the last 90 days. */
  author_removed_last_90_days: number
}

export type CommentDecisionRequest = {
  action: DecisionAction
  note: string
}

/** `{note}` for a restore (photo or comment). */
export type RestoreRequest = { note: string }

// ----------------------------------------------------------------- errors

export type AdminErrorCode =
  | 'forbidden'
  | 'conflict_of_interest'
  | 'case_not_found'
  | 'case_not_open'
  | 'not_restorable'
  | 'note_required'
  | 'rate_limited'

// ----------------------------------------------------------------- calls

export const adminApi = {
  // photo reports (§3.3 #11–#14)
  photoReports: ({ status, cursor, purpose }: ReportQueueParams) =>
    apiGet<ReportQueue<PhotoReportItem>>(
      `/admin/photo-reports${queryString({ status, cursor, purpose })}`,
    ),
  photoReport: (caseId: string) => apiGet<PhotoReportDetail>(`/admin/photo-reports/${seg(caseId)}`),
  decidePhotoReport: (caseId: string, data: PhotoDecisionRequest) =>
    apiPost<PhotoReportDetail>(`/admin/photo-reports/${seg(caseId)}/decision`, data),
  /** Returns the detail of the photo's last case (§3.4b 6). */
  restorePhoto: (photoId: string, data: RestoreRequest) =>
    apiPost<PhotoReportDetail>(`/admin/photos/${seg(photoId)}/restore`, data),

  // comment reports (§3.4a #14–#17)
  commentReports: ({ status, cursor }: ReportQueueParams) =>
    apiGet<ReportQueue<CommentReportItem>>(
      `/admin/comment-reports${queryString({ status, cursor })}`,
    ),
  commentReport: (caseId: string) =>
    apiGet<CommentReportDetail>(`/admin/comment-reports/${seg(caseId)}`),
  decideCommentReport: (caseId: string, data: CommentDecisionRequest) =>
    apiPost<CommentReportDetail>(`/admin/comment-reports/${seg(caseId)}/decision`, data),
  /** Returns the detail of the comment's last case (§3.4b 6). */
  restoreComment: (commentId: string, data: RestoreRequest) =>
    apiPost<CommentReportDetail>(`/admin/comments/${seg(commentId)}/restore`, data),
}

/** TanStack Query keys, all under `['admin', …]` (§6.4). */
export const adminKeys = {
  all: ['admin'] as const,
  photoReports: (status: ReportQueueStatus, purpose: string | null = null) =>
    purpose
      ? (['admin', 'photo-reports', status, purpose] as const)
      : (['admin', 'photo-reports', status] as const),
  photoReport: (caseId: string) => ['admin', 'photo-report', caseId] as const,
  commentReports: (status: ReportQueueStatus) => ['admin', 'comment-reports', status] as const,
  commentReport: (caseId: string) => ['admin', 'comment-report', caseId] as const,
}
