import { useId, useState, type FormEvent } from 'react'
import { ApiError, errorMessage } from '../../lib/api'
import type { CommentReportReason } from './feedApi'
import { useFeedOptions, useReportComment } from './feedQueries'
import { focusRing, primaryButton, Sheet, SheetCancel } from './feedUi'

/**
 * "Report this comment" (Q38, M3-DESIGN §6.3, interim copy): a reason from the config list,
 * optional details, then a thank-you. Reporting never hides the comment or tells its author.
 */
export function CommentReportSheet({
  commentId,
  onClose,
  onOnboardingRequired,
}: {
  commentId: string
  onClose: () => void
  /** 403 `onboarding_required`: the caller shows its prompt. */
  onOnboardingRequired: (error: unknown) => boolean
}) {
  const options = useFeedOptions()
  const report = useReportComment()
  const legendId = useId()
  const detailsId = useId()
  const [reason, setReason] = useState<CommentReportReason | null>(null)
  const [details, setDetails] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState(false)

  if (sent) {
    return (
      <Sheet title="Thanks for telling us" onClose={onClose}>
        <p className="text-slate-700">
          Thanks. Our team will review it. The comment stays up until they decide.
        </p>
        <button type="button" className={primaryButton} onClick={onClose}>
          Done
        </button>
      </Sheet>
    )
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!reason || report.isPending) return
    setError(null)
    const trimmed = details.trim()
    report.mutate(
      { commentId, report: trimmed ? { reason, details: trimmed } : { reason } },
      {
        onSuccess: () => setSent(true),
        onError: (err) => {
          if (onOnboardingRequired(err)) {
            onClose()
            return
          }
          setError(
            err instanceof ApiError && err.code === 'rate_limited'
              ? 'You’ve sent a lot of reports. Please try again later.'
              : errorMessage(err),
          )
        },
      },
    )
  }

  const reasons = options.data?.comment_report_reasons ?? []
  return (
    <Sheet title="Report this comment" onClose={onClose}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-3">
        <fieldset aria-labelledby={legendId} className="flex flex-col gap-0.5">
          <legend id={legendId} className="pb-1.5 text-[15px] text-slate-700">
            Why are you reporting it?
          </legend>
          {options.isPending && (
            <p role="status" className="text-sm text-slate-600">
              Loading…
            </p>
          )}
          {reasons.map((option) => (
            <label
              key={option.id}
              className={`flex min-h-12 cursor-pointer items-center gap-3 rounded-[10px] px-2 ${
                reason === option.id ? 'bg-brand-50' : ''
              }`}
            >
              <input
                type="radio"
                name="comment-report-reason"
                value={option.id}
                checked={reason === option.id}
                onChange={() => setReason(option.id)}
                className={`size-5 shrink-0 accent-brand-700 ${focusRing}`}
              />
              <span className="text-[15px] font-medium text-slate-900">{option.label}</span>
            </label>
          ))}
        </fieldset>
        <div className="flex flex-col gap-1">
          <label htmlFor={detailsId} className="text-sm font-medium text-slate-800">
            Add details (optional)
          </label>
          <textarea
            id={detailsId}
            rows={3}
            value={details}
            maxLength={options.data?.report_details_max}
            onChange={(event) => setDetails(event.target.value)}
            placeholder="Anything that helps our team decide"
            className={`rounded-lg border border-slate-200 px-3 py-2 text-[15px] ${focusRing}`}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm font-medium text-red-700">
            {error}
          </p>
        )}
        <button type="submit" className={primaryButton} disabled={!reason || report.isPending}>
          {report.isPending ? 'Sending…' : 'Send report'}
        </button>
        <SheetCancel onClick={onClose} />
      </form>
    </Sheet>
  )
}
