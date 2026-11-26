import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError } from '../../lib/api'
import { NotFoundPage } from '../../app/NotFoundPage'
import { formatDateTime } from './adminFormat'
import { AdminMain, AdminTitle, focusRing } from './adminUi'
import {
  fraudApi,
  fraudKeys,
  useFraudOptions,
  type AppealDetail,
  type AppealNoticeItem,
  type AppealOutcome,
} from './fraudApi'
import { KIND_LABELS, RELOAD_CODES, formatShortDate, fraudError } from './fraudFormat'
import { Chip, linkClass, NoteDialog, secondaryButtonClass } from './fraudParts'
import { BackToQueue, DoneBanner, ReviewCard } from './reviewParts'

const QUEUE = '/admin/appeals'

const STATUS_WORDS: Record<string, string> = {
  verified: 'Verified',
  pending: 'Pending',
  withdrawn: 'Withdrawn',
  rejected: 'Rejected',
}

/** What the person was told happened to the deed (D100 wording). */
function effectText(item: AppealNoticeItem): string {
  if (item.effect === 'excluded') return 'Not counted for leaderboards'
  if (item.effect === 'pending_removal') return 'Will be removed'
  if (item.effect === 'points_changed') {
    return item.old_points !== null && item.new_points !== null
      ? `Points changed from ${item.old_points} to ${item.new_points}`
      : 'Points changed'
  }
  return item.effect.replace(/_/g, ' ')
}

/**
 * `/admin/appeals/:appealId` (F25, M5-DESIGN §6.2, D100): the person's reason, what they were
 * told, the link to the case, and Uphold / Overturn with a note. The answer is final. The
 * original decider and conflicted admins get 404 (NotFound).
 */
export function AppealReviewPage() {
  const { appealId = '' } = useParams()
  const options = useFraudOptions().data
  const query = useQuery({
    queryKey: fraudKeys.appeal(appealId),
    queryFn: () => fraudApi.appeal(appealId),
    retry: false,
  })

  if (query.isPending) return <Loading label="Loading this appeal…" />
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundPage />
    return (
      <AdminMain>
        <BackToQueue to={QUEUE} label="Appeals" />
        <ErrorAlert>{fraudError(query.error, options)}</ErrorAlert>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className={`${secondaryButtonClass} self-start`}
        >
          Try again
        </button>
      </AdminMain>
    )
  }
  return <Review key={appealId} appeal={query.data} onReload={() => void query.refetch()} />
}

function Review({ appeal, onReload }: { appeal: AppealDetail; onReload: () => void }) {
  const queryClient = useQueryClient()
  const options = useFraudOptions().data
  const [choice, setChoice] = useState<AppealOutcome | null>(null)
  const decide = useMutation({
    mutationFn: ({ outcome, note }: { outcome: AppealOutcome; note: string }) =>
      fraudApi.decideAppeal(appeal.appeal_id, { outcome, note }),
    onSuccess: (next) => {
      setChoice(null)
      queryClient.setQueryData(fraudKeys.appeal(appeal.appeal_id), next)
      void queryClient.invalidateQueries({ queryKey: ['admin', 'fraud', 'appeals'] })
      void queryClient.invalidateQueries({ queryKey: fraudKeys.status })
      if (appeal.case_id) {
        void queryClient.invalidateQueries({ queryKey: fraudKeys.case(appeal.case_id) })
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code && RELOAD_CODES.has(error.code)) onReload()
    },
  })
  const name = appeal.user.display_name
  const notice = appeal.notice

  return (
    <AdminMain>
      <BackToQueue to={QUEUE} label="Appeals" />
      {appeal.outcome && (
        <DoneBanner
          title={appeal.outcome === 'overturned' ? 'Appeal overturned.' : 'Appeal upheld.'}
          body={
            appeal.outcome === 'overturned'
              ? `${name}’s deeds were restored.`
              : 'The decision stays.'
          }
          footnote={
            appeal.decided_at
              ? `Decided by ${appeal.reviewer?.display_name ?? 'an admin'} · ${formatDateTime(appeal.decided_at)}${
                  appeal.reviewer_note ? ` · “${appeal.reviewer_note}”` : ''
                }`
              : null
          }
        />
      )}
      <header className="flex flex-col gap-2">
        <AdminTitle>Appeal from {name}</AdminTitle>
        <div className="flex flex-wrap items-center gap-1.5 text-sm text-slate-600">
          <Chip tone="blue">{KIND_LABELS[appeal.kind]}</Chip>
          <span>
            @{appeal.user.handle} · asked {formatDateTime(appeal.created_at)} · original decision by{' '}
            {appeal.original_decider.display_name}
          </span>
        </div>
        {appeal.case_id && (
          <Link
            to={`/admin/fraud/${encodeURIComponent(appeal.case_id)}`}
            className={`${linkClass} self-start`}
          >
            Open the case
          </Link>
        )}
      </header>

      <div className="flex flex-wrap items-start gap-4">
        <div className="flex min-w-0 flex-[1_1_420px] flex-col gap-3.5">
          <ReviewCard title="Their reason">
            <blockquote className="border-l-4 border-slate-300 pl-3 text-[17px] break-words whitespace-pre-line text-slate-900">
              {appeal.reason}
            </blockquote>
          </ReviewCard>
          <ReviewCard title="What they were told">
            <p className="break-words text-slate-900">{notice.reason_text}</p>
            <p className="text-sm text-slate-600">
              Notice sent {formatShortDate(notice.created_at)} · appeal window until{' '}
              {formatShortDate(notice.appeal_until)}
            </p>
            <ul className="mt-1 flex flex-col">
              {notice.items.map((item, index) => (
                <li
                  key={item.deed_id}
                  className={`flex flex-col gap-1 ${index > 0 ? 'mt-2.5 border-t border-slate-200 pt-2.5' : ''}`}
                >
                  <p className="font-semibold break-words">
                    {item.reporter.display_name} → {item.doer.display_name}
                  </p>
                  <p className="text-sm text-slate-600">
                    {item.category_label} · {item.area_name}
                    {item.occurred_on ? ` · ${formatShortDate(item.occurred_on)}` : ''}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    <Chip tone="amber">{effectText(item)}</Chip>
                    {item.status && <Chip>{STATUS_WORDS[item.status] ?? item.status}</Chip>}
                  </div>
                  <Link
                    to={`/admin/scoring/deeds/${encodeURIComponent(item.deed_id)}`}
                    className={`${linkClass} self-start text-sm`}
                    aria-label={`Points and overrides for ${item.reporter.display_name} → ${item.doer.display_name}, ${item.category_label}`}
                  >
                    Points and overrides
                  </Link>
                </li>
              ))}
            </ul>
          </ReviewCard>
        </div>
        {!appeal.outcome && (
          <section
            aria-label="Decide"
            className="flex min-w-0 flex-[1_1_300px] flex-col gap-2.5 rounded-xl border border-slate-300 bg-white p-3.5"
          >
            <p className="text-slate-700">
              Uphold keeps the decision. Overturn restores {name}’s deeds and puts back any points
              that were cut. This answer is final.
            </p>
            <div className="grid grid-cols-2 gap-2.5">
              <button
                type="button"
                onClick={() => {
                  decide.reset()
                  setChoice('upheld')
                }}
                className={`min-h-12 rounded-lg border border-slate-300 bg-white text-base font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`}
              >
                Uphold
              </button>
              <button
                type="button"
                onClick={() => {
                  decide.reset()
                  setChoice('overturned')
                }}
                className={`min-h-12 rounded-lg bg-brand-700 text-base font-semibold text-white hover:bg-brand-900 ${focusRing}`}
              >
                Overturn
              </button>
            </div>
          </section>
        )}
      </div>

      {choice && (
        <NoteDialog
          title={choice === 'upheld' ? 'Uphold the decision?' : 'Overturn the decision?'}
          body={
            <>
              <p>
                {choice === 'upheld'
                  ? `The decision stays. ${name} is told “We looked again and the decision stays.”`
                  : `${name}’s deeds are restored: they count for leaderboards again, nothing is removed, and any points cut are put back. They’re told “We looked again and restored your deeds.”`}
              </p>
              <p className="font-semibold text-slate-900">This answer is final.</p>
            </>
          }
          confirmLabel={choice === 'upheld' ? 'Uphold' : 'Overturn'}
          busyLabel="Saving…"
          options={options}
          busy={decide.isPending}
          error={decide.isError ? fraudError(decide.error, options) : null}
          onCancel={() => setChoice(null)}
          onConfirm={(note) => decide.mutate({ outcome: choice, note })}
        />
      )}
    </AdminMain>
  )
}
