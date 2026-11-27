import { Avatar } from '../media/avatar/Avatar'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { formatAge } from './adminFormat'
import { AdminMain, AdminTitle } from './adminUi'
import {
  fraudApi,
  fraudKeys,
  useFraudOptions,
  type AppealQueueStatus,
  type AppealRow,
} from './fraudApi'
import { KIND_LABELS, fraudError } from './fraudFormat'
import { Chip, EmptyState, secondaryButtonClass, StatusTabs } from './fraudParts'

/**
 * `/admin/appeals` (F25, M5-DESIGN §6.2, D100): Open · n / Decided · n. Appeals this admin may
 * not decide (they made the decision, or they're conflicted) are left out by the API.
 */
export function AppealsPage() {
  const [params, setParams] = useSearchParams()
  const status: AppealQueueStatus = params.get('status') === 'decided' ? 'decided' : 'open'
  const options = useFraudOptions().data
  const fraudStatus = useQuery({ queryKey: fraudKeys.status, queryFn: fraudApi.status })
  const query = useInfiniteQuery({
    queryKey: fraudKeys.appeals(status),
    queryFn: ({ pageParam }) => fraudApi.appeals(status, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const counts = query.data?.pages[0]?.counts
  const items = query.data?.pages.flatMap((page) => page.items) ?? []
  const waiting = fraudStatus.data?.appeals_waiting_for_second_admin ?? 0

  return (
    <AdminMain>
      <AdminTitle>Appeals</AdminTitle>
      <StatusTabs
        label="Appeal status"
        value={status}
        onChange={(s) => setParams(s === 'open' ? {} : { status: s }, { replace: true })}
        tabs={[
          { id: 'open', label: 'Open', count: counts?.open ?? null },
          { id: 'decided', label: 'Decided', count: counts?.decided ?? null },
        ]}
      />
      {waiting > 0 && (
        <p
          role="status"
          className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900"
        >
          <strong>Appeals need a second admin · {waiting}</strong>{' '}
          {waiting === 1 ? 'One appeal has' : `${waiting} appeals have`} no admin who can decide it
          yet. Steps that can’t be undone stay paused until one can.
        </p>
      )}
      <p className="text-sm text-slate-600">
        {status === 'open'
          ? 'People asked for another review of a decision about their own deeds. Appeals about your own decisions, or about people you’re connected to, go to another admin.'
          : 'Most recently decided first.'}
      </p>
      {query.isPending ? (
        <Loading label="Loading appeals…" />
      ) : query.isError ? (
        <div className="flex flex-col items-start gap-3">
          <ErrorAlert>{fraudError(query.error, options)}</ErrorAlert>
          <button
            type="button"
            onClick={() => void query.refetch()}
            className={secondaryButtonClass}
          >
            Try again
          </button>
        </div>
      ) : items.length === 0 ? (
        <EmptyState title={status === 'open' ? 'No open appeals.' : 'No decided appeals yet.'} />
      ) : (
        <>
          <ul className="flex flex-col gap-2.5">
            {items.map((item) => (
              <li key={item.appeal_id}>
                <AppealQueueRow row={item} />
              </li>
            ))}
          </ul>
          {query.hasNextPage && (
            <button
              type="button"
              onClick={() => void query.fetchNextPage()}
              disabled={query.isFetchingNextPage}
              className={`${secondaryButtonClass} self-center`}
            >
              {query.isFetchingNextPage ? 'Loading…' : 'Show more'}
            </button>
          )}
        </>
      )}
    </AdminMain>
  )
}

function AppealQueueRow({ row }: { row: AppealRow }) {
  return (
    <div className="grid grid-cols-1 items-center gap-x-4 gap-y-3 rounded-[14px] border border-slate-200 bg-white p-3.5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]">
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap gap-1.5">
          <Chip tone="blue">{KIND_LABELS[row.kind]}</Chip>
          {row.outcome && (
            <Chip tone={row.outcome === 'overturned' ? 'green' : 'grey'}>
              {row.outcome === 'overturned' ? 'Overturned' : 'Upheld'}
            </Chip>
          )}
        </div>
        <span className="flex items-center gap-2 font-semibold break-words">
          <Avatar user={row.user} size="sm" />
          <span className="min-w-0">
            {row.user.display_name}{' '}
            <span className="font-normal text-slate-600">@{row.user.handle}</span>
          </span>
        </span>
      </div>
      <div className="min-w-0 text-sm text-slate-600">
        <span className="block">
          {row.deed_count === 1 ? '1 deed' : `${row.deed_count} deeds`} · original decision by{' '}
          {row.original_decider.display_name}
        </span>
        {row.decided_at
          ? `Answered ${formatAge(row.decided_at)}`
          : `Asked ${formatAge(row.created_at)}`}
      </div>
      <Link
        to={`/admin/appeals/${encodeURIComponent(row.appeal_id)}`}
        aria-label={`Review appeal from ${row.user.display_name}`}
        className={`${secondaryButtonClass} lg:justify-self-end`}
      >
        Review
      </Link>
    </div>
  )
}
