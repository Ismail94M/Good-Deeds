/**
 * Types and calls for the M3 media API (docs/M3-DESIGN.md §3.2, §3.3; backend/app/modules/media).
 *
 * `putToStorage` is the one call here that does NOT go through `src/lib/api.ts`: the signed
 * upload URL points at the storage host, which must never receive the access token or the
 * `X-Device-Id` header (§6.1, §6.4).
 */

import { ApiError, apiDelete, apiGet, apiPost } from '../../lib/api'
import type { UserSummary } from '../deeds/deedsApi'

const seg = encodeURIComponent

// ----------------------------------------------------------------- shapes (§3.2)

/** A public photo with signed URLs. Refetch the list before `urls_expire_at` (§6.4). */
export type PhotoOut = {
  id: string
  thumb_url: string
  display_url: string
  width: number
  height: number
  urls_expire_at: string
}

export type OwnerPhotoStatus = 'processing' | 'ready' | 'failed'

/** A photo as its uploader (or the doer of a verified deed) sees it in My deeds or a self-log. */
export type OwnerPhoto = {
  id: string
  position: number
  status: OwnerPhotoStatus
  thumb_url: string | null
  display_url: string | null
  blurred_count: number
  can_remove: boolean
}

/** Photo report reasons (§2.2 `photo_reports.reason`). Labels come from `MediaOptions`. */
export type PhotoReportReason =
  | 'not_consented'
  | 'child_no_guardian_consent'
  | 'private_details'
  | 'inappropriate'
  | 'other'
  /** Profile pictures only (M6 D121). */
  | 'impersonation'

/** A resolved review, or the doer's removal (Q29, reporter only). Open cases never show. */
export type Outcome = {
  decision: 'removed' | 'kept' | 'removed_by_doer'
  /** Set for an admin `removed` decision; the reason shown to the uploader. */
  removal_reason: PhotoReportReason | null
  /** Only for `removed_by_doer` (Q29). */
  removed_by: UserSummary | null
  decided_at: string
  photo_count: number
}

// ----------------------------------------------------------------- options

/** 1 = child, 2 = privacy and harm (Q44), 3 = other. */
export type ReportPriority = 1 | 2 | 3

export type ReportReasonOption = {
  id: PhotoReportReason
  label: string
  priority: ReportPriority
}

/** `GET /media/options`. Render photo controls only when `photos_enabled`. */
export type MediaOptions = {
  photos_enabled: boolean
  photos_per_owner: number
  photo_edit_window_hours: number
  upload_allowed_types: string[]
  upload_max_bytes: number
  blur_max_regions: number
  /** Max length of an admin's decision note (the admin note box, §3.4b 7). */
  decision_note_max: number
  report_reasons: ReportReasonOption[]
  /** Reasons for reporting a profile picture (M6 D121), in the order shown. */
  avatar_report_reasons?: ReportReasonOption[]
}

// ----------------------------------------------------------------- uploads

export type UploadPurpose = 'deed' | 'self_log'

export type CreateUploadRequest = {
  purpose: UploadPurpose
  content_type: string
  size_bytes: number
}

/** `POST /media/uploads` 201: where and how to PUT the file. */
export type UploadTicket = {
  photo_id: string
  upload: {
    url: string
    method: 'PUT'
    /** The signed headers (`Content-Type`, `Content-Length`); send exactly these. */
    headers: Record<string, string>
  }
  expires_at: string
}

export type CompleteUploadOut = {
  photo_id: string
  status: 'uploaded'
}

/** A blur ellipse in fractions of the displayed image (0..1, §4.5). */
export type BlurRegion = { x: number; y: number; w: number; h: number }

export type AttachPhoto = {
  photo_id: string
  /** Must be empty for self-log photos. */
  blur_regions: BlurRegion[]
}

export type AttachPhotosRequest = { photos: AttachPhoto[] }

export type OwnerPhotosOut = { photos: OwnerPhoto[] }

/** `GET /media/deeds/{id}/photos` (reporter, or the doer of a verified deed). */
export type DeedPhotosOut = OwnerPhotosOut & {
  can_add: boolean
  add_until: string | null
  outcomes: Outcome[]
}

// ----------------------------------------------------------------- reports

export type PhotoReportRequest = {
  reason: PhotoReportReason
  details?: string
}

/** 202, identical for a new report and a duplicate. */
export type ReportReceived = { status: 'received' }

// ----------------------------------------------------------------- errors

/** Error codes from the media endpoints (§3.3 table). */
export type MediaErrorCode =
  | 'unsupported_type'
  | 'too_large'
  | 'size_mismatch'
  | 'own_photo'
  | 'photos_disabled'
  | 'forbidden'
  | 'conflict_of_interest'
  | 'photo_not_found'
  | 'deed_not_found'
  | 'self_log_not_found'
  | 'case_not_found'
  | 'upload_missing'
  | 'photo_not_uploaded'
  | 'too_many_photos'
  | 'photo_window_closed'
  | 'deed_not_editable'
  | 'case_not_open'
  | 'not_restorable'
  | 'rate_limited'
  | 'too_many_pending_uploads'
  /** Client-side: the storage host refused or dropped the PUT (see `putToStorage`). */
  | 'upload_failed'

// ----------------------------------------------------------------- storage PUT

/**
 * PUT the file to the signed storage URL with plain `fetch` and only the signed headers: no
 * `Authorization`, no `X-Device-Id`, no cookies, no referrer.
 *
 * `Content-Length` is a header browsers set themselves from the body (scripts can't), so it
 * isn't passed on; instead the file's size must equal the signed length, or the storage would
 * refuse the upload anyway. Throws `ApiError` with code `upload_failed` when storage refuses
 * the PUT or the network drops, so callers can offer "Retry".
 */
export async function putToStorage(
  ticket: UploadTicket,
  file: Blob,
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  const headers: Record<string, string> = {}
  let signedLength: string | null = null
  for (const [name, value] of Object.entries(ticket.upload.headers)) {
    if (name.toLowerCase() === 'content-length') signedLength = value
    else headers[name] = value
  }
  if (signedLength !== null && Number(signedLength) !== file.size) {
    throw new ApiError(
      0,
      'That photo changed while uploading. Please add it again.',
      'size_mismatch',
    )
  }

  let response: Response
  try {
    response = await fetch(ticket.upload.url, {
      method: ticket.upload.method,
      headers,
      body: file,
      credentials: 'omit',
      mode: 'cors',
      referrerPolicy: 'no-referrer',
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(
      0,
      "The photo didn't upload. Check your connection and try again.",
      'upload_failed',
    )
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "The photo didn't upload. Please try again.",
      'upload_failed',
    )
  }
}

// ----------------------------------------------------------------- calls

export const mediaApi = {
  options: () => apiGet<MediaOptions>('/media/options'),

  // uploads (§3.3 #2–#4)
  createUpload: (data: CreateUploadRequest) => apiPost<UploadTicket>('/media/uploads', data),
  completeUpload: (photoId: string) =>
    apiPost<CompleteUploadOut>(`/media/uploads/${seg(photoId)}/complete`),
  discardUpload: (photoId: string) => apiDelete(`/media/uploads/${seg(photoId)}`),

  // attach (§3.3 #5, #6)
  attachToDeed: (deedId: string, data: AttachPhotosRequest) =>
    apiPost<OwnerPhotosOut>(`/media/deeds/${seg(deedId)}/photos`, data),
  attachToSelfLog: (selfLogId: string, data: AttachPhotosRequest) =>
    apiPost<OwnerPhotosOut>(`/media/self-logs/${seg(selfLogId)}/photos`, data),

  // owner reads (§3.3 #7, #8)
  deedPhotos: (deedId: string) => apiGet<DeedPhotosOut>(`/media/deeds/${seg(deedId)}/photos`),
  selfLogPhotos: (selfLogId: string) =>
    apiGet<OwnerPhotosOut>(`/media/self-logs/${seg(selfLogId)}/photos`),

  // remove (§3.3 #9): uploader any time (Q30), doer of a verified deed (Q29)
  deletePhoto: (photoId: string) => apiDelete(`/media/photos/${seg(photoId)}`),

  // report (§3.3 #10): auth optional
  reportPhoto: (photoId: string, data: PhotoReportRequest) =>
    apiPost<ReportReceived>(`/media/photos/${seg(photoId)}/reports`, data),
}

/** TanStack Query keys, all under `['media', …]` (§6.4). */
export const mediaKeys = {
  all: ['media'] as const,
  options: ['media', 'options'] as const,
  deed: (deedId: string) => ['media', 'deed', deedId] as const,
  selfLog: (selfLogId: string) => ['media', 'self-log', selfLogId] as const,
}
