import { useInfiniteQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import type { ReportPriority, ReportQueue as QueuePage, ReportQueueStatus } from './adminApi'
import { formatAge } from './adminFormat'
import { AdminMain, AdminTitle, focusRing, PriorityChip, type ChipKind } from './adminUi'

const RESOLVED_NOTE = 'Most recently decided first.'

export const secondaryButton = `inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-300 bg-white px-4.5 text-[15px] font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`

/**
 * A report queue page (photo and comment reports, M3-DESIGN §6.3): Open · n / Resolved · n tabs
 * in `?status=`, a note, the rows in the API's order (priority, then oldest), Show more, and the
 * loading, empty and error states.
 */
export function ReportQueue<T extends { case_id: string }>({
  title,
  openNote,
  noun,
  queryKey,
  fetchPage,
  renderRow,
  toolbar,
}: {
  title: string
  openNote: string
  /** "photo" / "comment", for the empty states. */
  noun: string
  queryKey: (status: ReportQueueStatus) => readonly unknown[]
  fetchPage: (status: ReportQueueStatus, cursor: string | null) => Promise<QueuePage<T>>
  renderRow: (item: T) => ReactNode
  /** Extra filters under the tabs (the photo queue's purpose filter, M6). */
  toolbar?: ReactNode
}) {
  const [params, setParams] = useSearchParams()
  const status: ReportQueueStatus = params.get('status') === 'resolved' ? 'resolved' : 'open'
  const query = useInfiniteQuery({
    queryKey: queryKey(status),
    queryFn: ({ pageParam }) => fetchPage(status, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const first = query.data?.pages[0]
  const items = query.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <AdminMain>
      <AdminTitle>{title}</AdminTitle>
      <QueueTabs
        status={status}
        openCount={first?.open_count ?? null}
        resolvedCount={first?.resolved_count ?? null}
        onChange={(next) => {
          const nextParams = new URLSearchParams(params)
          if (next === 'open') nextParams.delete('status')
          else nextParams.set('status', next)
          setParams(nextParams, { replace: true })
        }}
      />
      {toolbar}
      <p className="text-sm text-slate-600">{status === 'open' ? openNote : RESOLVED_NOTE}</p>
      {query.isPending ? (
        <Loading label="Loading reports…" />
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
            {status === 'open' ? `No open ${noun} reports` : 'No resolved reports yet'}
          </h2>
          <p className="text-slate-600">
            {status === 'open'
              ? 'New reports will appear here.'
              : 'Reports you and other reviewers decide will appear here.'}
          </p>
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-2.5">
            {items.map((item) => (
              <li key={item.case_id}>{renderRow(item)}</li>
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
    </AdminMain>
  )
}

/** "Open · {n}" / "Resolved · {n}" segmented tabs. */
function QueueTabs({
  status,
  openCount,
  resolvedCount,
  onChange,
}: {
  status: ReportQueueStatus
  openCount: number | null
  resolvedCount: number | null
  onChange: (status: ReportQueueStatus) => void
}) {
  const tabs: [ReportQueueStatus, string, number | null][] = [
    ['open', 'Open', openCount],
    ['resolved', 'Resolved', resolvedCount],
  ]
  return (
    <div
      role="tablist"
      aria-label="Report status"
      className="grid max-w-[360px] grid-cols-2 gap-1 rounded-xl bg-[#e9edf3] p-1"
    >
      {tabs.map(([id, label, count]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={status === id}
          onClick={() => onChange(id)}
          className={`min-h-11 rounded-[9px] text-sm font-semibold ${focusRing} ${
            status === id
              ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          {count === null ? label : `${label} · ${count}`}
        </button>
      ))}
    </div>
  )
}

/** One queue row: optional thumbnail, chip, top reason, a context line, counts, Review. */
export function QueueRow({
  thumb,
  priority,
  kind,
  reason,
  context,
  reportCount,
  oldestReportAt,
  href,
  reviewLabel,
}: {
  thumb?: ReactNode
  priority: ReportPriority
  kind: ChipKind
  reason: string
  context: ReactNode
  reportCount: number
  oldestReportAt: string
  href: string
  reviewLabel: string
}) {
  const age = formatAge(oldestReportAt)
  return (
    <div className="grid grid-cols-1 items-center gap-x-4 gap-y-3 rounded-[14px] border border-slate-200 bg-white p-3.5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_auto]">
      <div className="flex min-w-0 items-center gap-3">
        {thumb}
        <div className="flex min-w-0 flex-col gap-1">
          <PriorityChip priority={priority} kind={kind} />
          <span className="font-semibold break-words">{reason}</span>
        </div>
      </div>
      <div className="min-w-0 text-sm text-slate-600">
        <span className="block font-medium break-words text-slate-900">{context}</span>
        {reportCount === 1 ? `1 report · ${age}` : `${reportCount} reports · oldest ${age}`}
      </div>
      <Link to={href} aria-label={reviewLabel} className={`${secondaryButton} lg:justify-self-end`}>
        Review
      </Link>
    </div>
  )
}
