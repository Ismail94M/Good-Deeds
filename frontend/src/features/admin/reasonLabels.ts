import { useCallback } from 'react'
import type { CommentReportReason } from '../feed/feedApi'
import { useFeedOptions } from '../feed/feedQueries'
import type { PhotoReportReason, ReportPriority } from '../media/mediaApi'
import { useMediaOptions } from '../media/mediaQueries'
import { REASON_LABELS, REASON_PRIORITY } from '../media/mediaText'

/** Photo report reason labels and priorities from `/media/options`, with built-in fallbacks. */
export function useReasonLabel() {
  const reasons = useMediaOptions().data?.report_reasons
  return useCallback(
    (reason: PhotoReportReason) =>
      reasons?.find((r) => r.id === reason)?.label ?? REASON_LABELS[reason] ?? reason,
    [reasons],
  )
}

export function useReasonPriority() {
  const reasons = useMediaOptions().data?.report_reasons
  return useCallback(
    (reason: PhotoReportReason): ReportPriority =>
      reasons?.find((r) => r.id === reason)?.priority ?? REASON_PRIORITY[reason] ?? 3,
    [reasons],
  )
}

/** Comment report reasons (§6.3 "Report comment", Q38); `/feed/options` overrides these. */
export const COMMENT_REASON_LABELS: Record<CommentReportReason, string> = {
  threat_or_harassment: 'Threatening or harassing',
  hate: 'Hateful',
  personal_info: 'Shares someone’s personal information',
  spam: 'Spam or scam',
  other: 'Something else',
}

/** §2.3: threats and personal details first, then hate, then spam and the rest. */
const COMMENT_REASON_PRIORITY: Record<CommentReportReason, ReportPriority> = {
  threat_or_harassment: 1,
  personal_info: 1,
  hate: 2,
  spam: 3,
  other: 3,
}

export function useCommentReasons() {
  const reasons = useFeedOptions().data?.comment_report_reasons
  const label = useCallback(
    (reason: CommentReportReason) =>
      reasons?.find((r) => r.id === reason)?.label ?? COMMENT_REASON_LABELS[reason] ?? reason,
    [reasons],
  )
  const priority = useCallback(
    (reason: CommentReportReason): ReportPriority =>
      reasons?.find((r) => r.id === reason)?.priority ?? COMMENT_REASON_PRIORITY[reason] ?? 3,
    [reasons],
  )
  return { label, priority }
}
