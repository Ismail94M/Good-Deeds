import { Link } from 'react-router'
import { firstName } from '../report/format'
import type { Outcome } from './mediaApi'
import { photoCount, REMOVAL_REASON_TEXT } from './mediaText'

/**
 * A review outcome on the reporter's My deeds detail (photos canvas §3, M-OutcomeRemoved /
 * M-OutcomeKept; docs/M3-DESIGN.md §6.3). Open cases are never shown: the API only sends resolved
 * ones, and the doer's removals (Q29) as a neutral line with no reason and no button.
 */
export function PhotoOutcomeBanner({
  outcome,
  doerName,
}: {
  outcome: Outcome
  /** The doer's display name (the deed is verified when photos can be reported). */
  doerName: string
}) {
  const n = outcome.photo_count
  if (outcome.decision === 'removed_by_doer') {
    const who = outcome.removed_by?.display_name ?? doerName
    return (
      <p
        role="status"
        className="rounded-xl border border-slate-200 bg-slate-100 px-3.5 py-3 text-[15px] text-slate-700"
      >
        {who} removed {photoCount(n)} from this deed.
      </p>
    )
  }
  if (outcome.decision === 'kept') {
    return (
      <div
        role="status"
        className="flex items-start gap-2.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3.5 text-emerald-800"
      >
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="mt-0.5 size-5 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
        <span>
          <strong className="block text-emerald-950">Review complete: your photos stay</strong>
          Someone reported a photo on this deed. Our team reviewed it and it stays up. No action
          needed.
        </span>
      </div>
    )
  }
  const reason = outcome.removal_reason
    ? REMOVAL_REASON_TEXT[outcome.removal_reason]
    : REMOVAL_REASON_TEXT.other
  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-3.5 text-amber-900"
    >
      <div className="flex items-start gap-2.5">
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="mt-0.5 size-5 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7.5v5.5M12 16.5h.01" />
        </svg>
        <span>
          <strong className="block text-amber-950">
            {n === 1 ? 'A photo was removed after review' : `${n} photos were removed after review`}
          </strong>
          Our team removed {photoCount(n)} because {reason}. Your deed is still verified, and{' '}
          {firstName(doerName)}’s count and points haven’t changed.
        </span>
      </div>
      <Link
        to="/photo-guidelines"
        className="ml-7.5 inline-flex min-h-11 items-center self-start text-[15px] font-semibold text-amber-800 underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700"
      >
        Read the photo guidelines
      </Link>
    </div>
  )
}
