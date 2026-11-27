import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { Avatar } from '../media/avatar/Avatar'
import { ErrorAlert, Loading } from '../../components/ui'
import { formatAge } from './adminFormat'
import { AdminMain, AdminTitle } from './adminUi'
import {
  caseTypeLabel,
  fraudApi,
  fraudKeys,
  useFraudOptions,
  type CaseQueueStatus,
  type CaseRow,
  type FraudOptions,
} from './fraudApi'
import { fraudError, namesLine } from './fraudFormat'
import {
  Chip,
  DeadlineChip,
  EmptyState,
  FilterChip,
  secondaryButtonClass,
  StatusTabs,
} from './fraudParts'

export const QUEUE_NOTE =
  'A case is a pattern worth a look, not proof. Nothing changes for anyone until you decide. Decide before the month’s boards become final if you can.'

const STATUSES: CaseQueueStatus[] = ['open', 'parked', 'resolved']

const EMPTY: Record<CaseQueueStatus, string> = {
  open: 'No open cases.',
  parked: 'No parked cases.',
  resolved: 'No resolved cases yet.',
}

function readStatus(value: string | null): CaseQueueStatus {
  return STATUSES.includes(value as CaseQueueStatus) ? (value as CaseQueueStatus) : 'open'
}

/**
 * `/admin/fraud` (F21, M5-DESIGN §6.2): Open / Parked / Resolved tabs with counts, type filter
 * chips, the note, and the cases in the API's order (deadline first; resolved newest first).
 */
export function FraudCasesPage() {
  const [params, setParams] = useSearchParams()
  const status = readStatus(params.get('status'))
  const type = params.get('type')
  const options = useFraudOptions().data
  const query = useInfiniteQuery({
    queryKey: fraudKeys.cases(status, type),
    queryFn: ({ pageParam }) => fraudApi.cases({ status, type, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const counts = query.data?.pages[0]?.counts
  const items = query.data?.pages.flatMap((page) => page.items) ?? []
  const types = options?.types.filter((t) => t.enabled) ?? []

  const update = (next: { status?: CaseQueueStatus; type?: string | null }) => {
    const s = next.status ?? status
    const t = next.type === undefined ? type : next.type
    const search: Record<string, string> = {}
    if (s !== 'open') search.status = s
    if (t) search.type = t
    setParams(search, { replace: true })
  }

  return (
    <AdminMain>
      <AdminTitle>Fraud cases</AdminTitle>
      <StatusTabs
        label="Case status"
        value={status}
        onChange={(s) => update({ status: s })}
        tabs={[
          { id: 'open', label: 'Open', count: counts?.open ?? null },
          { id: 'parked', label: 'Parked', count: counts?.parked ?? null },
          { id: 'resolved', label: 'Resolved', count: counts?.resolved ?? null },
        ]}
      />
      {types.length > 0 && (
        <div role="group" aria-label="Case type" className="flex flex-wrap gap-2">
          <FilterChip pressed={type === null} onClick={() => update({ type: null })}>
            All types
          </FilterChip>
          {types.map((t) => (
            <FilterChip key={t.id} pressed={type === t.id} onClick={() => update({ type: t.id })}>
              {t.label}
            </FilterChip>
          ))}
        </div>
      )}
      <p className="text-sm text-slate-600">
        {status === 'resolved' ? 'Most recently decided first.' : QUEUE_NOTE}
      </p>
      {query.isPending ? (
        <Loading label="Loading cases…" />
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
        <EmptyState title={EMPTY[status]} />
      ) : (
        <>
          <ul className="flex flex-col gap-2.5">
            {items.map((item) => (
              <li key={item.case_id}>
                <CaseQueueRow row={item} options={options} />
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

function CaseQueueRow({ row, options }: { row: CaseRow; options: FraudOptions | undefined }) {
  const resolved = row.status === 'resolved'
  return (
    <div className="grid grid-cols-1 items-center gap-x-4 gap-y-3 rounded-[14px] border border-slate-200 bg-white p-3.5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]">
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap gap-1.5">
          <Chip tone="blue">{caseTypeLabel(options, row.type)}</Chip>
          {row.status === 'parked' && <Chip>Parked</Chip>}
          {resolved && (
            <Chip tone={row.outcome === 'confirmed' ? 'red' : 'green'}>
              {row.outcome === 'confirmed' ? 'Confirmed' : 'Dismissed'}
            </Chip>
          )}
          {!resolved && row.review_by && <DeadlineChip at={row.review_by} />}
        </div>
        <span className="font-semibold break-words">{row.summary}</span>
      </div>
      <div className="min-w-0 text-sm text-slate-600">
        <span className="flex items-center gap-2 font-medium break-words text-slate-900">
          {/* M6 §6.2 (D124): the subjects' avatars, up to 3, overlapping. */}
          <span aria-hidden className="flex shrink-0 -space-x-2">
            {row.users.slice(0, 3).map((u) => (
              <Avatar key={u.handle} user={u} size="xs" className="ring-2 ring-white" />
            ))}
          </span>
          <span className="min-w-0">{namesLine(row.users, row.user_count)}</span>
        </span>
        {row.deed_count === 1 ? '1 deed' : `${row.deed_count} deeds`} · {row.held_count} held
        <span className="block">
          {resolved && row.resolved_at
            ? `Decided ${formatAge(row.resolved_at)}`
            : `Opened ${formatAge(row.opened_at)}`}
        </span>
      </div>
      <Link
        to={`/admin/fraud/${encodeURIComponent(row.case_id)}`}
        aria-label={`Review case: ${row.summary}`}
        className={`${secondaryButtonClass} lg:justify-self-end`}
      >
        Review
      </Link>
    </div>
  )
}
