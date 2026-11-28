import { Avatar } from '../media/avatar/Avatar'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { AdminDialog, DialogActions, fieldClass } from './AdminDialog'
import { formatDateTime } from './adminFormat'
import { AdminMain, AdminTitle, focusRing } from './adminUi'
import { secondaryButton } from './ReportQueue'
import {
  scoringAdminApi,
  scoringAdminKeys,
  type OverrideDecision,
  type OverrideRequest,
  type OverrideRequestTab,
} from './scoringAdminApi'
import { CharCount } from './scoringAdminParts'
import {
  personName,
  requestDecisionError,
  roleLabel,
  useScoringFraudOptions,
} from './scoringAdminText'

const STATUS_TEXT: Record<OverrideRequest['status'], string> = {
  pending: 'Waiting',
  approved: 'Approved',
  declined: 'Declined',
  expired: 'Expired',
}

/**
 * `/admin/scoring/override-requests` (M5 F22, Q77): big point cuts waiting for a second admin.
 * Tabs Pending · n / Decided · n, rows with the person, the deed, old → new points, reason, note,
 * who asked and when it expires, and Approve / Decline with a required note. The API leaves out
 * the admin's own requests and conflicted ones.
 */
export function OverrideRequestsPage() {
  const [params, setParams] = useSearchParams()
  const tab: OverrideRequestTab = params.get('status') === 'decided' ? 'decided' : 'pending'
  const queryClient = useQueryClient()
  const options = useScoringFraudOptions()
  const [deciding, setDeciding] = useState<{
    request: OverrideRequest
    decision: OverrideDecision
  } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const query = useInfiniteQuery({
    queryKey: scoringAdminKeys.requests(tab),
    queryFn: ({ pageParam }) => scoringAdminApi.requests(tab, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const first = query.data?.pages[0]
  const items = query.data?.pages.flatMap((page) => page.items) ?? []
  const reasonLabel = (code: string) =>
    options.data?.override_reason_codes.find((r) => r.id === code)?.label ?? code

  const tabs: [OverrideRequestTab, string, number | undefined][] = [
    ['pending', 'Pending', first?.counts.pending],
    ['decided', 'Decided', first?.counts.decided],
  ]

  return (
    <AdminMain>
      <AdminTitle>Override requests</AdminTitle>
      <div
        role="tablist"
        aria-label="Request status"
        className="grid max-w-[360px] grid-cols-2 gap-1 rounded-xl bg-[#e9edf3] p-1"
      >
        {tabs.map(([id, label, count]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => {
              setNotice(null)
              setParams(id === 'pending' ? {} : { status: id }, { replace: true })
            }}
            className={`min-h-11 rounded-[9px] text-sm font-semibold ${focusRing} ${
              tab === id
                ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            {count === undefined ? label : `${label} · ${count}`}
          </button>
        ))}
      </div>
      <p className="text-sm text-slate-600">
        {tab === 'pending'
          ? 'Big changes to points, and cuts to 0, need a second admin. Nothing changes until one approves.'
          : 'Most recently decided first.'}
      </p>
      {notice && (
        <p role="status" className="text-sm font-medium text-slate-800">
          {notice}
        </p>
      )}
      {query.isPending ? (
        <Loading label="Loading requests…" />
      ) : query.isError ? (
        <div className="flex flex-col items-start gap-3">
          <ErrorAlert>{errorMessage(query.error)}</ErrorAlert>
          <button type="button" onClick={() => void query.refetch()} className={secondaryButton}>
            Try again
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-10 text-center">
          <h2 className="text-lg font-bold text-slate-900">
            {tab === 'pending' ? 'No requests waiting' : 'No decided requests yet'}
          </h2>
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-2.5">
            {items.map((request) => (
              <li key={request.id}>
                <RequestRow
                  request={request}
                  reasonLabel={reasonLabel(request.reason_code)}
                  onDecide={(decision) => {
                    setNotice(null)
                    setDeciding({ request, decision })
                  }}
                />
              </li>
            ))}
          </ul>
          {query.hasNextPage && (
            <button
              type="button"
              onClick={() => void query.fetchNextPage()}
              disabled={query.isFetchingNextPage}
              className={`${secondaryButton} self-center`}
            >
              {query.isFetchingNextPage ? 'Loading…' : 'Show more'}
            </button>
          )}
        </>
      )}
      {deciding && (
        <DecideDialog
          request={deciding.request}
          decision={deciding.decision}
          noteMin={options.data?.note_min_chars ?? 5}
          noteMax={options.data?.note_max_chars ?? 1000}
          onDone={(message) => {
            setDeciding(null)
            setNotice(message)
            void queryClient.invalidateQueries({ queryKey: scoringAdminKeys.allRequests })
          }}
          onStale={() =>
            void queryClient.invalidateQueries({ queryKey: scoringAdminKeys.allRequests })
          }
          onCancel={() => setDeciding(null)}
        />
      )}
    </AdminMain>
  )
}

function RequestRow({
  request,
  reasonLabel,
  onDecide,
}: {
  request: OverrideRequest
  reasonLabel: string
  onDecide: (decision: OverrideDecision) => void
}) {
  const who = personName(request.user)
  return (
    <article className="flex flex-col gap-1.5 rounded-[14px] border border-slate-200 bg-white p-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="flex flex-wrap items-center gap-x-2 text-[17px] font-bold break-words text-slate-900">
          {request.user && <Avatar user={request.user} size="sm" />}
          {request.user?.handle ? (
            <Link
              to={`/admin/users/${encodeURIComponent(request.user.handle)}`}
              className={`hover:text-brand-700 ${focusRing}`}
            >
              {who}
            </Link>
          ) : (
            who
          )}{' '}
          <span className="font-normal text-slate-600">· {roleLabel(request.role)}</span>
        </h2>
        {request.status !== 'pending' && (
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[13px] font-semibold text-slate-700">
            {STATUS_TEXT[request.status]}
          </span>
        )}
      </div>
      <p className="text-[15px] font-semibold">
        Points {request.previous_points} → {request.points}
      </p>
      <p className="text-sm text-slate-700">
        {reasonLabel} · asked by {personName(request.requested_by)},{' '}
        {formatDateTime(request.created_at)}
      </p>
      <p className="text-sm break-words text-slate-800">{request.note}</p>
      <p className="text-sm text-slate-600">
        {request.status === 'pending'
          ? `Expires ${formatDateTime(request.expires_at)}`
          : request.decided_at
            ? `Decided by ${personName(request.decided_by)}, ${formatDateTime(request.decided_at)}`
            : `Expired ${formatDateTime(request.expires_at)}`}
      </p>
      {request.decision_note && (
        <p className="text-sm break-words text-slate-700">Note: {request.decision_note}</p>
      )}
      <Link
        to={`/admin/scoring/deeds/${encodeURIComponent(request.deed_id)}`}
        className={`inline-flex min-h-11 items-center self-start font-semibold text-brand-700 hover:text-brand-900 ${focusRing}`}
      >
        See the deed’s points
      </Link>
      {request.status === 'pending' && (
        <div className="grid grid-cols-2 gap-2 border-t border-slate-200 pt-2.5 sm:flex">
          <button
            type="button"
            onClick={() => onDecide('approve')}
            aria-label={`Approve: ${who}, ${request.previous_points} to ${request.points} points`}
            className={`min-h-11 rounded-lg bg-brand-700 px-4 font-semibold text-white hover:bg-brand-900 ${focusRing}`}
          >
            Approve
          </button>
          <button
            type="button"
            onClick={() => onDecide('decline')}
            aria-label={`Decline: ${who}, ${request.previous_points} to ${request.points} points`}
            className={secondaryButton}
          >
            Decline
          </button>
        </div>
      )}
    </article>
  )
}

function DecideDialog({
  request,
  decision,
  noteMin,
  noteMax,
  onDone,
  onStale,
  onCancel,
}: {
  request: OverrideRequest
  decision: OverrideDecision
  noteMin: number
  noteMax: number
  onDone: (message: string) => void
  onStale: () => void
  onCancel: () => void
}) {
  const noteId = useId()
  const countId = useId()
  const [note, setNote] = useState('')
  const [tooShort, setTooShort] = useState(false)
  const approve = decision === 'approve'
  const mutation = useMutation({
    mutationFn: () => scoringAdminApi.decide(request.id, decision, note.trim()),
    onSuccess: () =>
      onDone(approve ? 'Approved. The points have been changed.' : 'Declined. Nothing changed.'),
    onError: onStale,
  })
  const busy = mutation.isPending
  return (
    <AdminDialog
      title={approve ? 'Approve this change?' : 'Decline this change?'}
      onDismiss={onCancel}
      busy={busy}
    >
      {({ bodyId }) => (
        <>
          <p id={bodyId} className="text-slate-700">
            {personName(request.user)} ({roleLabel(request.role).toLowerCase()}): points{' '}
            {request.previous_points} → {request.points}.{' '}
            {approve
              ? 'The change applies as soon as you approve.'
              : 'The points stay as they are.'}
          </p>
          <div className="flex flex-col gap-1">
            <label htmlFor={noteId} className="text-[15px] font-semibold">
              Note for the record <span className="font-normal text-slate-600">(required)</span>
            </label>
            <textarea
              id={noteId}
              rows={3}
              value={note}
              maxLength={noteMax}
              onChange={(e) => setNote(e.target.value)}
              aria-describedby={countId}
              aria-invalid={tooShort || undefined}
              className={fieldClass}
            />
            <CharCount id={countId} value={note} max={noteMax} />
          </div>
          {(tooShort || mutation.isError) && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {tooShort
                ? `Add a note of at least ${noteMin} characters.`
                : requestDecisionError(mutation.error)}
            </p>
          )}
          <DialogActions
            confirm={busy ? 'Saving…' : approve ? 'Approve' : 'Decline'}
            danger={false}
            busy={busy}
            onConfirm={() => {
              if (note.trim().length < noteMin) {
                setTooShort(true)
                return
              }
              setTooShort(false)
              mutation.mutate()
            }}
            onCancel={onCancel}
          />
        </>
      )}
    </AdminDialog>
  )
}
