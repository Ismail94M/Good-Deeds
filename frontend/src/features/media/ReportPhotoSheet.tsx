import { useMutation } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { focusRing } from '../deeds/deedUi'
import type { ReportPhotoSheetProps } from '../feed/reportPhotoSlot'
import { mediaApi, type PhotoReportReason } from './mediaApi'
import { useMediaOptions } from './mediaQueries'
import { REASON_LABELS } from './mediaText'

/** `report_details_max` (500); `/media/options` doesn't carry it and visitors can't read options. */
const DETAILS_MAX = 500

const REASON_ORDER: PhotoReportReason[] = [
  'not_consented',
  'child_no_guardian_consent',
  'private_details',
  'inappropriate',
  'other',
]

function reportError(error: unknown): string {
  const code = error instanceof ApiError ? error.code : null
  if (code === 'rate_limited')
    return 'Too many reports from this connection. Please try again later.'
  if (code === 'own_photo')
    return 'You can’t report a photo you posted. You can remove it from My deeds.'
  if (error instanceof ApiError && error.status === 404) {
    return 'This photo isn’t available any more.'
  }
  return errorMessage(error)
}

const button = `min-h-12 w-full rounded-lg text-base font-semibold disabled:cursor-not-allowed ${focusRing}`

/**
 * "Report this photo" (photos canvas §2, M-ReportSheet / M-ReportSent; docs/M3-DESIGN.md §6.3)
 * for members and visitors (auth optional, §3.3 #10). Reasons come from `/media/options` when the
 * viewer can read it, else the built-in labels. Sending never hides the photo: the sheet only
 * thanks the reporter, the same way for a new report and a duplicate.
 */
export function ReportPhotoSheet({ photoId, onClose }: ReportPhotoSheetProps) {
  const { user } = useAuth()
  const media = useMediaOptions(Boolean(user?.onboarding.complete))
  const titleId = useId()
  const detailsId = useId()
  const ref = useRef<HTMLDivElement>(null)
  const [reason, setReason] = useState<PhotoReportReason | null>(null)
  const [details, setDetails] = useState('')
  const send = useMutation({
    mutationFn: () =>
      mediaApi.reportPhoto(photoId, {
        reason: reason as PhotoReportReason,
        ...(details.trim() ? { details: details.trim() } : {}),
      }),
  })

  const reasons = REASON_ORDER.map((id) => ({
    id,
    label: media.data?.report_reasons.find((r) => r.id === id)?.label ?? REASON_LABELS[id],
  }))

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('h2')?.focus()
    return () => opener?.focus()
  }, [])

  // Move focus to the thank-you heading once sent.
  useEffect(() => {
    if (send.isSuccess) ref.current?.querySelector<HTMLElement>('h2')?.focus()
  }, [send.isSuccess])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = ref.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input, textarea',
    )
    if (!focusable || focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (reason && !send.isPending) send.mutate()
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-slate-900/50 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[92svh] w-full max-w-[480px] flex-col gap-2.5 overflow-y-auto rounded-t-2xl bg-white px-4 pt-3 pb-5 shadow-[0_-8px_32px_rgba(15,23,42,0.25)] sm:rounded-2xl"
      >
        <span aria-hidden className="h-1 w-10 self-center rounded-full bg-slate-300" />
        {send.isSuccess ? (
          <>
            <div className="flex flex-col items-center gap-2.5 px-1 pt-2 pb-1 text-center">
              <span
                aria-hidden
                className="flex size-13 items-center justify-center rounded-full bg-emerald-50 text-emerald-700"
              >
                <svg
                  viewBox="0 0 24 24"
                  className="size-7"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M5 12.5l4.5 4.5L19 7.5" />
                </svg>
              </span>
              <h2
                id={titleId}
                tabIndex={-1}
                className="text-[19px] font-bold text-slate-900 outline-none"
              >
                Thanks for telling us
              </h2>
              <p role="status" className="text-slate-700">
                Our team will review this photo. Reports about children and privacy are looked at
                first.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className={`${button} bg-brand-700 text-white hover:bg-brand-900`}
            >
              Done
            </button>
          </>
        ) : (
          <form noValidate onSubmit={submit} className="flex flex-col gap-2.5">
            <h2
              id={titleId}
              tabIndex={-1}
              className="mt-1 text-lg font-bold text-slate-900 outline-none"
            >
              Report this photo
            </h2>
            <fieldset className="flex min-w-0 flex-col gap-0.5">
              <legend className="pb-1.5 text-[15px] text-slate-700">
                Why are you reporting it?
              </legend>
              {reasons.map((option) => (
                <label
                  key={option.id}
                  className={`flex min-h-12 cursor-pointer items-center gap-3 rounded-[10px] px-2 ${
                    reason === option.id ? 'bg-brand-50' : 'hover:bg-slate-50'
                  }`}
                >
                  <input
                    type="radio"
                    name="photo-report-reason"
                    value={option.id}
                    checked={reason === option.id}
                    onChange={() => setReason(option.id)}
                    className={`size-5 shrink-0 accent-brand-700 ${focusRing}`}
                  />
                  <span className="text-[15px] font-medium text-slate-900">{option.label}</span>
                </label>
              ))}
            </fieldset>
            <label htmlFor={detailsId} className="text-[15px] font-semibold">
              Add details <span className="font-normal text-slate-600">(optional)</span>
            </label>
            <textarea
              id={detailsId}
              rows={2}
              value={details}
              maxLength={DETAILS_MAX}
              onChange={(event) => setDetails(event.target.value)}
              placeholder="Anything that helps our team decide"
              className={`w-full resize-none rounded-lg border border-slate-300 px-3 py-2.5 text-[15px] ${focusRing}`}
            />
            <p className="rounded-[10px] bg-slate-50 px-3 py-2.5 text-sm text-slate-600">
              Our team reviews every report. The photo stays up until they decide. The person who
              posted it won’t see who reported it.
            </p>
            {send.isError && (
              <p role="alert" className="text-sm font-medium text-red-700">
                {reportError(send.error)}
              </p>
            )}
            <button
              type="submit"
              disabled={!reason || send.isPending}
              className={`${button} bg-brand-700 text-white hover:bg-brand-900 disabled:bg-slate-400`}
            >
              {send.isPending ? 'Sending…' : 'Send report'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className={`${button} border border-slate-200 bg-white text-slate-800 hover:bg-slate-50`}
            >
              Cancel
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
