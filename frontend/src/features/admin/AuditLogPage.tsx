import { useInfiniteQuery } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { fieldClass } from './AdminDialog'
import { formatDateTime } from './adminFormat'
import { AdminMain, AdminTitle, focusRing } from './adminUi'
import { fraudApi, fraudKeys, useFraudOptions, type AuditFilters, type AuditItem } from './fraudApi'
import { fraudError } from './fraudFormat'
import { EmptyState, secondaryButtonClass } from './fraudParts'

/** The filter fields, as they appear in the page's own URL (`?entity_type=…&from=2026-11-01`). */
const FIELDS = ['entity_type', 'entity_id', 'actor', 'action', 'from', 'to'] as const
type Field = (typeof FIELDS)[number]
type Draft = Record<Field, string>

const DATE = /^\d{4}-\d{2}-\d{2}$/
/** IST, the app's time zone: a date filter covers that whole day there. */
const IST = '+05:30'

function readDraft(params: URLSearchParams): Draft {
  return Object.fromEntries(FIELDS.map((f) => [f, params.get(f) ?? ''])) as Draft
}

/** The URL's filters as the API takes them; a date becomes the start or end of that IST day. */
function toFilters(draft: Draft): AuditFilters {
  const filters: AuditFilters = {}
  const text = (value: string) => value.trim() || undefined
  filters.entity_type = text(draft.entity_type)
  filters.entity_id = text(draft.entity_id)
  filters.actor = text(draft.actor)?.replace(/^@/, '')
  filters.action = text(draft.action)
  if (DATE.test(draft.from)) filters.from = `${draft.from}T00:00:00${IST}`
  if (DATE.test(draft.to)) filters.to = `${draft.to}T23:59:59.999${IST}`
  for (const key of Object.keys(filters) as (keyof AuditFilters)[]) {
    if (filters[key] === undefined) delete filters[key]
  }
  return filters
}

/**
 * `/admin/audit` (F24, M5-DESIGN D96, §6.2): filters (entity type, entity id, actor handle,
 * action, date range), entries newest first with their old and new values in an expandable
 * row, and Load more.
 */
export function AuditLogPage() {
  const [params, setParams] = useSearchParams()
  const applied = readDraft(params)
  const filters = toFilters(applied)
  const options = useFraudOptions().data
  const query = useInfiniteQuery({
    queryKey: fraudKeys.audit(filters),
    queryFn: ({ pageParam }) => fraudApi.audit(filters, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const items = query.data?.pages.flatMap((page) => page.items) ?? []

  const apply = (draft: Draft) => {
    const search: Record<string, string> = {}
    for (const f of FIELDS) if (draft[f].trim()) search[f] = draft[f].trim()
    setParams(search, { replace: true })
  }

  return (
    <AdminMain>
      <AdminTitle>Audit log</AdminTitle>
      <FilterForm key={params.toString()} initial={applied} onApply={apply} />
      {query.isPending ? (
        <Loading label="Loading the audit log…" />
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
        <EmptyState title="No entries match these filters." />
      ) : (
        <>
          <p className="text-sm text-slate-600">Newest first.</p>
          <ul className="flex flex-col gap-2">
            {items.map((item) => (
              <li key={item.id}>
                <AuditRow item={item} />
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
              {query.isFetchingNextPage ? 'Loading…' : 'Load more'}
            </button>
          )}
        </>
      )}
    </AdminMain>
  )
}

const LABELS: Record<Field, string> = {
  entity_type: 'Entity type',
  entity_id: 'Entity ID',
  actor: 'Actor handle',
  action: 'Action',
  from: 'From',
  to: 'To',
}

const PLACEHOLDERS: Partial<Record<Field, string>> = {
  entity_type: 'e.g. flag_case',
  action: 'e.g. flag_case.confirmed',
  actor: 'e.g. priya',
}

function FilterForm({ initial, onApply }: { initial: Draft; onApply: (draft: Draft) => void }) {
  const id = useId()
  const [draft, setDraft] = useState(initial)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onApply(draft)
  }
  const empty = Object.fromEntries(FIELDS.map((f) => [f, ''])) as Draft
  return (
    <form
      onSubmit={submit}
      aria-label="Filter the audit log"
      className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-3.5"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {FIELDS.map((f) => (
          <div key={f} className="flex min-w-0 flex-col gap-1">
            <label htmlFor={`${id}-${f}`} className="text-sm font-semibold">
              {LABELS[f]}
            </label>
            <input
              id={`${id}-${f}`}
              type={f === 'from' || f === 'to' ? 'date' : 'text'}
              value={draft[f]}
              placeholder={PLACEHOLDERS[f]}
              maxLength={100}
              onChange={(event) => setDraft({ ...draft, [f]: event.target.value })}
              className={`${fieldClass} min-h-11 min-w-0`}
            />
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          className={`inline-flex min-h-11 items-center justify-center rounded-lg bg-brand-700 px-4 text-[15px] font-semibold text-white hover:bg-brand-900 ${focusRing}`}
        >
          Apply filters
        </button>
        <button
          type="button"
          className={secondaryButtonClass}
          onClick={() => {
            setDraft(empty)
            onApply(empty)
          }}
        >
          Clear
        </button>
      </div>
    </form>
  )
}

function AuditRow({ item }: { item: AuditItem }) {
  const [open, setOpen] = useState(false)
  const detailsId = useId()
  const hasValues = item.old_value !== null || item.new_value !== null
  return (
    <div className="flex flex-col gap-2 rounded-[14px] border border-slate-200 bg-white p-3.5">
      <div className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm lg:grid-cols-[11rem_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-center">
        <span className="text-slate-600">{formatDateTime(item.created_at)}</span>
        <span className="min-w-0 font-semibold break-all text-slate-900">{item.action}</span>
        <span className="min-w-0 break-all text-slate-700">
          {item.entity_type} · {item.entity_id}
        </span>
        <span className="text-slate-700">
          {item.actor ? `${item.actor.display_name} @${item.actor.handle}` : 'System'}
        </span>
      </div>
      {item.reason && (
        <p className="text-sm break-words whitespace-pre-line text-slate-700">{item.reason}</p>
      )}
      {hasValues && (
        <>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen(!open)}
            className={`-my-1 inline-flex min-h-11 items-center self-start rounded-md text-sm font-semibold text-brand-700 hover:text-brand-900 ${focusRing}`}
          >
            {open ? 'Hide values' : 'Show values'}
          </button>
          <div id={detailsId} hidden={!open} className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            <JsonBlock label="Old value" value={item.old_value} />
            <JsonBlock label="New value" value={item.new_value} />
          </div>
        </>
      )}
    </div>
  )
}

function JsonBlock({ label, value }: { label: string; value: Record<string, unknown> | null }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-bold text-slate-600">{label}</span>
      <pre className="max-h-72 overflow-auto rounded-lg bg-slate-50 p-2.5 text-xs break-words whitespace-pre-wrap text-slate-800">
        {value === null ? '—' : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  )
}
