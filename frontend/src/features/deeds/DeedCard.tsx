import { Link } from 'react-router'
import { useUnreadOutcome } from '../media/outcomeSeen'
import { formatCardDate } from '../report/format'
import type { Category, DeedOut } from './deedsApi'
import { useCanWithdraw } from './withdrawWindow'
import { categoryLabel, extraWitnessNote, isExtraWitness, whoLine } from './deedText'
import {
  EditIcon,
  focusRing,
  smallButtonClass,
  smallDangerButtonClass,
  StatusBadge,
} from './deedUi'

/**
 * One deed in My deeds (F6 canvas): status badge, date (" · Edited"), who line (opens the
 * detail), category and description (3 lines). Recognised for adds the Q20 extra-witness note
 * on reports that don't count; I reported adds Edit and Withdraw when allowed, and a dot when a
 * photo review outcome is unread on this device (M3-DESIGN §6.3, _interim_).
 */
export function DeedCard({
  deed,
  viewer,
  categories,
  sameActCap,
  onWithdraw,
}: {
  deed: DeedOut
  viewer: 'doer' | 'reporter'
  categories: Category[] | undefined
  sameActCap: number
  onWithdraw: (deed: DeedOut) => void
}) {
  // M4 D64: the Withdraw button goes once the 24 h window passes, even with the page open.
  const canWithdraw = useCanWithdraw(deed)
  const showActions = viewer === 'reporter' && (deed.can_edit || canWithdraw)
  const unread = useUnreadOutcome(deed, viewer === 'reporter')
  return (
    <article className="flex flex-col gap-1.5 rounded-2xl border border-slate-200 bg-white px-4 py-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusBadge status={deed.status} />
        <span className="text-[13px] text-[#586579]">
          {formatCardDate(deed.occurred_at)}
          {deed.last_edited_at ? ' · Edited' : ''}
        </span>
      </div>
      <h2>
        <Link
          to={`/me/deeds/${encodeURIComponent(deed.id)}`}
          className={`flex min-h-11 items-center rounded text-[17px] font-bold text-slate-900 hover:text-brand-700 ${focusRing}`}
        >
          {whoLine(deed, viewer)}
          {unread && (
            <>
              <span aria-hidden className="ml-2 size-2.5 shrink-0 rounded-full bg-brand-700" />
              <span className="sr-only"> (new photo review update)</span>
            </>
          )}
        </Link>
      </h2>
      <p className="text-sm font-semibold text-brand-900">
        {categoryLabel(categories, deed.category)}
      </p>
      <p className="line-clamp-3 text-[15px] break-words text-slate-700">{deed.description}</p>
      {viewer === 'doer' && isExtraWitness(deed) && (
        <p className="rounded-lg bg-brand-50 px-2.5 py-2 text-sm text-brand-900">
          {extraWitnessNote(sameActCap)}
        </p>
      )}
      {showActions && (
        <div className="mt-1 grid grid-cols-2 gap-2 border-t border-slate-200 pt-2.5">
          {deed.can_edit ? (
            <Link
              to={`/me/deeds/${encodeURIComponent(deed.id)}/edit`}
              className={smallButtonClass}
              aria-label={`Edit: ${whoLine(deed, viewer)}`}
            >
              <EditIcon />
              Edit
            </Link>
          ) : (
            <span />
          )}
          {canWithdraw && (
            <button
              type="button"
              onClick={() => onWithdraw(deed)}
              className={smallDangerButtonClass}
              aria-label={`Withdraw: ${whoLine(deed, viewer)}`}
            >
              Withdraw
            </button>
          )}
        </div>
      )}
    </article>
  )
}
