import { Link } from 'react-router'
import type { Category, DeedOut, UserSummary } from '../deeds/deedsApi'
import { formatDeadline } from '../media/mediaText'
import { firstName, formatOccurredAt } from './format'
import { CheckIcon, primaryButton, ReportMain, ScreenTitle, secondaryButton } from './reportUi'

/**
 * After Send (F3 canvas, M-Success / M-SuccessExtra): "Deed verified" when the report counts,
 * "Saved as an extra witness" when the act already has its counted reports
 * (`same_act.counts_for_points` false). With photos (M3-DESIGN §6.3): "Your photos will appear in
 * a few moments.", or, when attaching failed, where to add them until the window closes.
 */
export function ReportResult({
  deed,
  doer,
  reporterName,
  categories,
  photos = null,
}: {
  deed: DeedOut
  /** From the scan; the deed's own `doer` is preferred when present. */
  doer: UserSummary
  reporterName: string | null
  categories: Category[]
  /** What happened to the photos picked on the form; null when none were picked. */
  photos?: PhotoResult | null
}) {
  const who = deed.doer ?? doer
  const counts = deed.same_act?.counts_for_points ?? true
  const category = categories.find((c) => c.slug === deed.category)?.label ?? deed.category
  const thanks = reporterName ? `Thanks, ${firstName(reporterName)}. ` : 'Thanks. '

  return (
    <ReportMain>
      <div className="flex flex-col items-center gap-4 pt-6 text-center">
        {counts ? (
          <span
            aria-hidden
            className="flex size-19 items-center justify-center rounded-full bg-emerald-50 text-emerald-700"
          >
            <CheckIcon className="size-10" />
          </span>
        ) : (
          <span
            aria-hidden
            className="flex size-19 items-center justify-center rounded-full bg-brand-50 text-brand-900"
          >
            <svg
              viewBox="0 0 24 24"
              className="size-9.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="9" cy="8" r="3.5" />
              <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
              <path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6.5 6.5 0 0 1 3.5 6" />
            </svg>
          </span>
        )}
        <ScreenTitle autoFocus>
          {counts ? 'Deed verified' : 'Saved as an extra witness'}
        </ScreenTitle>
        <p className="max-w-[420px] text-slate-700">
          {counts
            ? `${thanks}It now counts on ${who.display_name}’s profile.`
            : `Others already reported this act. Your report backs theirs up, but it doesn’t count a second time on ${firstName(who.display_name)}’s profile.`}
        </p>
        {photos && <PhotoResultLine photos={photos} />}
        <dl className="mt-2 grid w-full grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2.5 rounded-2xl border border-slate-200 bg-white px-4.5 py-4 text-left text-[15px]">
          <dt className="text-slate-600">Who</dt>
          <dd className="font-semibold break-words">{who.display_name}</dd>
          <dt className="text-slate-600">Deed</dt>
          <dd className="font-semibold">{category}</dd>
          <dt className="text-slate-600">When</dt>
          <dd className="font-semibold">{formatOccurredAt(deed.occurred_at)}</dd>
          <dt className="text-slate-600">Checked by</dt>
          <dd className="font-semibold">QR code and location</dd>
        </dl>
        <div className="mt-2 flex w-full flex-col gap-2.5">
          <Link to="/report" className={primaryButton}>
            Record another deed
          </Link>
          <Link to="/me/deeds?tab=reported" className={secondaryButton}>
            See my reports
          </Link>
        </div>
      </div>
    </ReportMain>
  )
}

/** Photos attached after Send; `failed` when some or all of them didn't make it (§6.3). */
export type PhotoResult = {
  attached: number
  failed: boolean
  /** `reported_at + photo_edit_window_hours`: until when My deeds can add photos. */
  addUntil: string
}

function PhotoResultLine({ photos }: { photos: PhotoResult }) {
  const until = formatDeadline(photos.addUntil)
  return (
    <div role="status" className="flex max-w-[420px] flex-col gap-1 text-slate-700">
      {photos.attached > 0 && <p>Your photos will appear in a few moments.</p>}
      {photos.failed && (
        <p className="font-medium text-amber-800">
          {photos.attached > 0
            ? `Some photos didn’t upload. You can add them from My deeds until ${until}.`
            : `Your deed was sent, but the photos didn’t upload. You can add them from My deeds until ${until}.`}
        </p>
      )}
    </div>
  )
}
