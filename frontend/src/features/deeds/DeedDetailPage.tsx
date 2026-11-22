import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { feedApi, feedKeys } from '../feed/feedApi'
import { DeedPhotos } from '../media/OwnerPhotos'
import { formatOccurredAt, formatShortDate } from '../report/format'
import { useDeedOptions } from './deedQueries'
import { DEFAULT_SAME_ACT_CAP, deedsApi, deedsKeys, type DeedOut } from './deedsApi'
import {
  categoryLabel,
  checkedBy,
  extraWitnessNote,
  isExtraWitness,
  viewerRole,
  visibleDoerName,
  whoLine,
} from './deedText'
import {
  BackLink,
  Chip,
  EditIcon,
  EmptyState,
  F6Main,
  F6Title,
  HeartIcon,
  primaryButtonClass,
  secondaryButtonClass,
  StatusBadge,
} from './deedUi'
import { useScoringOptions } from '../scoring/scoringQueries'
import { WithdrawDeedDialog } from './WithdrawDeedDialog'
import { formatWithdrawUntil, useCanWithdraw, windowClosedNote } from './withdrawWindow'

/** Passed in navigation state, e.g. from the edit page after a 409 `deed_not_editable`. */
export type DeedDetailState = { notice?: string } | null

/**
 * `/me/deeds/:id` (F6 canvas): the private owner view of one deed. The reporter gets Edit and
 * Withdraw while allowed, and the withdrawn notice. M3: photos with review outcomes, remove and
 * add (`DeedPhotos`, M3-DESIGN §6.3), and a "Shown in feed" / "Hidden from feed" chip for verified
 * deeds (read from the feed's deed detail).
 */
export function DeedDetailPage() {
  const { id = '' } = useParams()
  const location = useLocation()
  const notice = (location.state as DeedDetailState)?.notice ?? null
  const { user } = useAuth()
  const options = useDeedOptions()
  const query = useQuery({
    queryKey: deedsKeys.deed(id),
    queryFn: () => deedsApi.deed(id),
    retry: false,
  })

  const verified = query.data?.status === 'verified'
  const feedDeed = useQuery({
    queryKey: feedKeys.deed(id),
    queryFn: () => feedApi.deed(id),
    enabled: verified,
    retry: false,
  })
  const visibility = verified ? feedDeed.data?.visibility : null

  if (query.isPending) return <Loading label="Loading this deed…" />
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <F6Main>
          <BackLink to="/me/deeds" label="My deeds" />
          <EmptyState
            icon={<HeartIcon />}
            title="This deed isn’t available"
            body="It may have been withdrawn, or the link is wrong."
            action={
              <Link to="/me/deeds" className={primaryButtonClass}>
                Back to My deeds
              </Link>
            }
          />
        </F6Main>
      )
    }
    return (
      <F6Main>
        <BackLink to="/me/deeds" label="My deeds" />
        <ErrorAlert>{errorMessage(query.error)}</ErrorAlert>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className={`${secondaryButtonClass} self-start`}
        >
          Try again
        </button>
      </F6Main>
    )
  }

  const deed = query.data
  const viewer = viewerRole(deed, user?.handle)
  const back = viewer === 'reporter' ? '/me/deeds?tab=reported' : '/me/deeds'
  const cap = options.data?.same_act_cap ?? DEFAULT_SAME_ACT_CAP
  const doerName = visibleDoerName(deed) ?? 'them'

  return (
    <F6Main>
      <BackLink to={back} label="My deeds" />
      {notice && (
        <p
          role="alert"
          className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-[15px] text-amber-800"
        >
          {notice}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={deed.status} />
        {deed.last_edited_at && <Chip>Edited</Chip>}
        {visibility && <Chip>{visibility.listed ? 'Shown in feed' : 'Hidden from feed'}</Chip>}
      </div>
      <div className="flex flex-col gap-1">
        <F6Title>{whoLine(deed, viewer)}</F6Title>
        <p className="font-semibold text-brand-900">
          {categoryLabel(options.data?.categories, deed.category)}
        </p>
      </div>
      <p className="break-words whitespace-pre-line text-slate-800">{deed.description}</p>
      <DeedPhotos deed={deed} viewer={viewer} />
      {viewer === 'doer' && isExtraWitness(deed) && (
        <p className="rounded-lg bg-brand-50 px-3 py-2.5 text-sm text-brand-900">
          {extraWitnessNote(cap)}
        </p>
      )}
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2.5 rounded-2xl border border-slate-200 bg-white px-4.5 py-4 text-[15px]">
        {deed.beneficiaries_count !== null && (
          <>
            <dt className="text-slate-600">People helped</dt>
            <dd className="font-semibold">{deed.beneficiaries_count}</dd>
          </>
        )}
        <dt className="text-slate-600">When</dt>
        <dd className="font-semibold">{formatOccurredAt(deed.occurred_at)}</dd>
        <dt className="text-slate-600">Checked by</dt>
        <dd className="font-semibold">{checkedBy(deed)}</dd>
        {deed.last_edited_at && (
          <>
            <dt className="text-slate-600">Last edited</dt>
            <dd className="font-semibold">{formatOccurredAt(deed.last_edited_at)}</dd>
          </>
        )}
      </dl>
      {viewer === 'reporter' && deed.status === 'withdrawn' && deed.withdrawn_at && (
        <div
          role="status"
          className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-slate-100 px-3.5 py-3 text-[15px] text-slate-700"
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="mt-0.5 size-5 shrink-0"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 11v5M12 8h.01" />
          </svg>
          <span>
            You withdrew this report on {formatShortDate(deed.withdrawn_at)}. It no longer counts
            for {doerName} and it has left the public feed.
          </span>
        </div>
      )}
      {viewer === 'reporter' && <ReporterActions deed={deed} />}
    </F6Main>
  )
}

/**
 * The reporter's Edit and Withdraw (F6), with the M4 withdrawal window (D64, §6.3): inside it
 * "You can withdraw until 3 Dec, 14:05" and the button; after it the button is gone and a note
 * says why. A pending invite deed can be withdrawn at any time.
 */
function ReporterActions({ deed }: { deed: DeedOut }) {
  const [withdrawing, setWithdrawing] = useState(false)
  const canWithdraw = useCanWithdraw(deed)
  const verified = deed.status === 'verified'
  const windowClosed = verified && deed.withdrawable_until !== null && !canWithdraw
  const scoringOptions = useScoringOptions(windowClosed)
  return (
    <>
      {verified && canWithdraw && deed.withdrawable_until && (
        <p className="text-[15px] text-slate-700">
          You can withdraw until {formatWithdrawUntil(deed.withdrawable_until)}.
        </p>
      )}
      {windowClosed && (
        <p className="text-[15px] text-slate-600">
          {windowClosedNote(scoringOptions.data?.withdraw_window_hours)}
        </p>
      )}
      {(deed.can_edit || canWithdraw) && (
        <div className="grid grid-cols-2 gap-2">
          {deed.can_edit ? (
            <Link
              to={`/me/deeds/${encodeURIComponent(deed.id)}/edit`}
              className={secondaryButtonClass}
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
              onClick={() => setWithdrawing(true)}
              className={`${secondaryButtonClass} border-red-200 text-red-700 hover:bg-red-50`}
            >
              Withdraw
            </button>
          )}
        </div>
      )}
      {withdrawing && <WithdrawDeedDialog deed={deed} onClose={() => setWithdrawing(false)} />}
    </>
  )
}
