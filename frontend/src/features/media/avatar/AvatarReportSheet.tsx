import { useMutation } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import { ApiError, errorMessage } from '../../../lib/api'
import { focusRing, Sheet } from '../../feed/feedUi'
import { useMediaOptions } from '../mediaQueries'
import { AVATAR_REPORT_REASONS, avatarApi, type AvatarReportReason } from './avatarApi'

const DETAILS_MAX = 500

function reportError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return 'This profile picture isn’t available any more.'
    if (error.status === 429) return 'You’ve sent a lot of reports. Please try again later.'
  }
  return errorMessage(error)
}

const button = `min-h-12 w-full rounded-lg text-base font-semibold disabled:cursor-not-allowed ${focusRing}`

/**
 * "Report profile picture" (§6.3, D121): signed-in members pick a reason ("Shows a child" first)
 * and optional details. The report joins the photo-report queue; the picture stays up until a
 * moderator decides, and the owner never sees who reported it.
 */
export function AvatarReportSheet({
  handle,
  name,
  onClose,
}: {
  handle: string
  name: string
  onClose: () => void
}) {
  const options = useMediaOptions()
  const detailsId = useId()
  const [reason, setReason] = useState<AvatarReportReason | null>(null)
  const [details, setDetails] = useState('')
  const send = useMutation({
    mutationFn: async () => {
      const { photo_id } = await avatarApi.reportTarget(handle)
      return avatarApi.report(photo_id, reason as AvatarReportReason, details.trim() || undefined)
    },
  })
  const fromServer = options.data?.avatar_report_reasons
  const reasons =
    fromServer && fromServer.length > 0
      ? fromServer.map((r) => ({ id: r.id as AvatarReportReason, label: r.label }))
      : AVATAR_REPORT_REASONS

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (reason && !send.isPending) send.mutate()
  }

  if (send.isSuccess) {
    return (
      <Sheet title="Thanks for telling us" onClose={onClose}>
        <p role="status" className="text-slate-700">
          Thanks. Someone from our team will look at it. Reports about children are looked at first.
        </p>
        <button
          type="button"
          onClick={onClose}
          className={`${button} bg-brand-700 text-white hover:bg-brand-900`}
        >
          Done
        </button>
      </Sheet>
    )
  }

  return (
    <Sheet title="Report profile picture" subtitle={`${name}’s profile picture`} onClose={onClose}>
      <form noValidate onSubmit={submit} className="flex flex-col gap-2.5">
        <fieldset className="flex min-w-0 flex-col gap-0.5">
          <legend className="pb-1.5 text-[15px] text-slate-700">Why are you reporting it?</legend>
          {reasons.map((option) => (
            <label
              key={option.id}
              className={`flex min-h-12 cursor-pointer items-center gap-3 rounded-[10px] px-2 ${
                reason === option.id ? 'bg-brand-50' : 'hover:bg-slate-50'
              }`}
            >
              <input
                type="radio"
                name="avatar-report-reason"
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
          The picture stays up until our team decides. {name} won’t see who reported it.
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
    </Sheet>
  )
}
