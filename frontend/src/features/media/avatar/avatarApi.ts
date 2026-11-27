/**
 * Profile pictures (docs/M6-DESIGN.md §3.3 P1–P6, D116–D122; backend/app/modules/media). A
 * `UserRef.avatar` is an opaque ref; only `POST /media/avatars/urls` turns refs into short-lived
 * signed URLs, for signed-in viewers (D119).
 */

import { apiDelete, apiGet, apiPost, apiPut } from '../../../lib/api'
import type { ReportReceived, UploadTicket } from '../mediaApi'

const seg = encodeURIComponent

/** Signed URLs of one live picture (P5). Swap them before `expires_at` (D118). */
export type AvatarUrls = {
  sm_url: string
  md_url: string
  lg_url: string
  expires_at: string
}

/** P5 response: a null item means the ref isn't live any more, or its owner is inactive. */
export type AvatarUrlsOut = { items: Record<string, AvatarUrls | null> }

/** A normalised square on the oriented image (§2.2 `photos.avatar_crop`). */
export type AvatarCrop = { x: number; y: number; size: number }

/** Why the worker refused a picture (`photos.reject_reason`). */
export type AvatarRejectReason =
  'unsupported_type' | 'too_large' | 'too_many_pixels' | 'animated' | 'corrupt' | 'size_mismatch'

/** P3 `GET /media/me/avatar`. */
export type MyAvatar = {
  current: AvatarUrls | null
  pending: {
    status: 'processing' | 'rejected' | 'failed'
    reject_reason?: AvatarRejectReason | null
  } | null
  /** Set after an admin removal (Q95): why, and when a new picture may be added (24 h). */
  removed_notice: { removed_at: string; reason_label: string; upload_after?: string } | null
}

/** Reasons offered when reporting a profile picture (D121), children first. */
export type AvatarReportReason =
  'child_no_guardian_consent' | 'impersonation' | 'private_details' | 'inappropriate' | 'other'

export const AVATAR_REPORT_REASONS: { id: AvatarReportReason; label: string }[] = [
  { id: 'child_no_guardian_consent', label: 'Shows a child' },
  { id: 'impersonation', label: 'Not this person / impersonation' },
  { id: 'private_details', label: 'Private details' },
  { id: 'inappropriate', label: 'Inappropriate or harmful' },
  { id: 'other', label: 'Other' },
]

export const avatarApi = {
  /** P5: one batched call per screen (see `useAvatarUrls`). */
  urls: (refs: string[]) => apiPost<AvatarUrlsOut>('/media/avatars/urls', { refs }),
  /** P1 with `purpose: "avatar"`. */
  createUpload: (contentType: string, sizeBytes: number) =>
    apiPost<UploadTicket>('/media/uploads', {
      purpose: 'avatar',
      content_type: contentType,
      size_bytes: sizeBytes,
    }),
  completeUpload: (photoId: string) => apiPost<unknown>(`/media/uploads/${seg(photoId)}/complete`),
  /** P2: 202 `{status: "processing"}`. */
  setMine: (photoId: string, crop: AvatarCrop) =>
    apiPut<{ status: 'processing' }>('/media/me/avatar', { photo_id: photoId, crop }),
  /** P3. */
  mine: () => apiGet<MyAvatar>('/media/me/avatar'),
  /** P4: deleted at once. */
  removeMine: () => apiDelete('/media/me/avatar'),
  /** P6 step 1: the photo to report, for a person with a live picture (404 otherwise). */
  reportTarget: (handle: string) =>
    apiGet<{ photo_id: string }>(`/users/${seg(handle)}/avatar-report-target`),
  /** P6 step 2: the existing photo-report endpoint. */
  report: (photoId: string, reason: AvatarReportReason, details?: string) =>
    apiPost<ReportReceived>(`/media/photos/${seg(photoId)}/reports`, {
      reason,
      ...(details ? { details } : {}),
    }),
}

export const avatarKeys = {
  url: (ref: string) => ['avatar', ref] as const,
  mine: ['me', 'avatar'] as const,
}
