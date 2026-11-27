/**
 * Review-page parts shared by photo reports (F17) and comment reports (F19): cards, the report
 * list, decision history, the Decide box, the done banner and the error copy (M3-DESIGN §6.3).
 */

import { Avatar } from '../media/avatar/Avatar'
import type { ReactNode } from 'react'
import { useId } from 'react'
import { Link } from 'react-router'
import { ErrorAlert } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import type { DecisionRecord, ReportPriority } from './adminApi'
import { NOTE_TOO_SHORT } from './AdminDialog'
import { formatAge, formatDateTime } from './adminFormat'
import { focusRing, PriorityChip, type ChipKind } from './adminUi'

export const DEFAULT_NOTE_MAX = 1000

const backLinkClass = `-my-2 inline-flex min-h-11 items-center gap-1 self-start rounded-md pr-2 text-[15px] font-semibold text-brand-700 hover:text-brand-900 ${focusRing}`

export function BackToQueue({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className={backLinkClass}>
      ‹ {label}
    </Link>
  )
}

/** A titled white card, labelled by its heading. */
export function ReviewCard({
  title,
  children,
  className = '',
}: {
  title: string
  children: ReactNode
  className?: string
}) {
  const id = useId()
  return (
    <section
      aria-labelledby={id}
      className={`flex flex-col gap-1.5 rounded-xl border border-slate-200 bg-white p-3.5 ${className}`}
    >
      <h2 id={id} className="text-[15px] font-bold text-slate-700">
        {title}
      </h2>
      {children}
    </section>
  )
}

export type ReportEntryView = {
  priority: ReportPriority
  reason: string
  details: string | null
  /** "Member @rahulv" or "Visitor on a shared link". */
  who: string
  createdAt: string
}

export function ReportsCard({ reports, kind }: { reports: ReportEntryView[]; kind: ChipKind }) {
  return (
    <ReviewCard title={`Reports (${reports.length})`} className="gap-2.5">
      <ul className="flex flex-col">
        {reports.map((report, index) => (
          <li
            key={index}
            className={`flex flex-col gap-1 ${index > 0 ? 'mt-2.5 border-t border-slate-200 pt-2.5' : ''}`}
          >
            <PriorityChip priority={report.priority} kind={kind} />
            <p className="font-semibold">{report.reason}</p>
            {report.details && (
              <p className="text-sm break-words text-slate-700">“{report.details}”</p>
            )}
            <p className="text-[13px] text-slate-600">
              {report.who} · {formatAge(report.createdAt)}
            </p>
          </li>
        ))}
      </ul>
    </ReviewCard>
  )
}

const ACTION_LABELS = { keep: 'Kept', remove: 'Removed', restore: 'Restored' } as const

export function DecisionHistory({ decisions }: { decisions: DecisionRecord[] }) {
  if (decisions.length === 0) return null
  return (
    <ReviewCard title="Decisions">
      <ul className="flex flex-col gap-2">
        {decisions.map((record, index) => (
          <li key={index} className="text-sm">
            <p className="flex items-center gap-2 font-semibold text-slate-900">
              <Avatar user={record.actor} size="xs" />
              <span className="min-w-0">
                {ACTION_LABELS[record.action]} by {record.actor.display_name} ·{' '}
                {formatDateTime(record.created_at)}
              </span>
            </p>
            <p className="break-words text-slate-700">{record.note}</p>
          </li>
        ))}
      </ul>
    </ReviewCard>
  )
}

/** "Note for the record (required)", the help line, and Keep / Remove. */
export function DecideSection({
  note,
  onNote,
  noteMax,
  noteTooShort,
  error,
  help,
  keepLabel,
  removeLabel,
  busy,
  onKeep,
  onRemove,
}: {
  note: string
  onNote: (note: string) => void
  noteMax: number
  noteTooShort: boolean
  error: string | null
  help: string
  keepLabel: string
  removeLabel: string
  busy: boolean
  onKeep: () => void
  onRemove: () => void
}) {
  const id = useId()
  return (
    <section className="flex flex-col gap-2.5 rounded-xl border border-slate-300 bg-white p-3.5">
      <label htmlFor={id} className="font-bold">
        Note for the record <span className="font-normal text-slate-600">(required)</span>
      </label>
      <textarea
        id={id}
        rows={3}
        value={note}
        maxLength={noteMax}
        onChange={(event) => onNote(event.target.value)}
        placeholder="Why you decided this"
        aria-invalid={noteTooShort || undefined}
        aria-describedby={`${id}-help`}
        className={`w-full rounded-lg border bg-white px-3 py-2.5 text-[15px] ${focusRing} ${
          noteTooShort ? 'border-2 border-red-700' : 'border-slate-300'
        }`}
      />
      <p id={`${id}-help`} className="text-sm text-slate-600">
        {help}
      </p>
      {(noteTooShort || error) && (
        <p role="alert" className="text-sm font-medium text-red-700">
          {noteTooShort ? NOTE_TOO_SHORT : error}
        </p>
      )}
      <div className="grid grid-cols-2 gap-2.5">
        <button
          type="button"
          disabled={busy}
          onClick={onKeep}
          className={`min-h-12 rounded-lg border border-slate-300 bg-white text-base font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60 ${focusRing}`}
        >
          {keepLabel}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onRemove}
          className={`min-h-12 rounded-lg bg-red-700 text-base font-semibold text-white hover:bg-red-800 disabled:opacity-60 ${focusRing}`}
        >
          {removeLabel}
        </button>
      </div>
    </section>
  )
}

export function RestoreButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-h-11 rounded-lg border border-slate-300 bg-white px-4 text-[15px] font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`}
    >
      {label}
    </button>
  )
}

/** The banner shown once a case is decided (D-AdminDone), with Restore while allowed. */
export function DoneBanner({
  title,
  body,
  action,
  footnote,
}: {
  title: string
  body: string
  action?: ReactNode
  footnote: string | null
}) {
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-4 gap-y-2.5 rounded-xl border border-slate-300 bg-slate-100 px-4 py-3.5 text-slate-800"
    >
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        className="size-5 shrink-0"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M5 12.5l4.5 4.5L19 7.5" />
      </svg>
      <span className="flex-[1_1_320px]">
        <strong>{title}</strong> {body}
        {footnote && <span className="mt-1 block text-sm text-slate-600">{footnote}</span>}
      </span>
      {action}
    </div>
  )
}

/** Not found or failed to load: the back link, then a message (with Try again on errors). */
export function ReviewLoadError({
  error,
  backTo,
  backLabel,
  onRetry,
}: {
  error: unknown
  backTo: string
  backLabel: string
  onRetry: () => void
}) {
  const missing = error instanceof ApiError && error.status === 404
  return (
    <>
      <BackToQueue to={backTo} label={backLabel} />
      {missing ? (
        <>
          <h1 className="text-[26px] leading-tight font-bold tracking-tight text-slate-900 sm:text-[32px]">
            This report isn’t available
          </h1>
          <p className="text-slate-600">It may have been closed, or the link is wrong.</p>
        </>
      ) : (
        <>
          <ErrorAlert>{errorMessage(error)}</ErrorAlert>
          <button
            type="button"
            onClick={onRetry}
            className={`min-h-11 self-start rounded-lg border border-slate-300 bg-white px-4 font-semibold ${focusRing}`}
          >
            Try again
          </button>
        </>
      )}
    </>
  )
}
