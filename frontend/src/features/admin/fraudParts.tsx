/**
 * Parts shared by the fraud admin screens (M5-DESIGN §6.2): the queue tabs, chips, the note
 * dialog and the dialog shell.
 */

import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { fieldClass } from './AdminDialog'
import { formatDateTime } from './adminFormat'
import { focusRing } from './adminUi'
import type { FraudOptions } from './fraudApi'
import { DEFAULT_NOTE_MAX, isUrgent, useNote } from './fraudFormat'

// ----------------------------------------------------------------- chips

type Tone = 'grey' | 'red' | 'amber' | 'green' | 'blue'

const TONES: Record<Tone, string> = {
  grey: 'bg-slate-100 text-slate-700',
  red: 'bg-red-100 text-red-800',
  amber: 'bg-amber-100 text-amber-900',
  green: 'bg-emerald-50 text-emerald-800',
  blue: 'bg-brand-50 text-brand-900',
}

export function Chip({ tone = 'grey', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center self-start rounded-full px-2 py-px text-xs font-bold whitespace-nowrap ${TONES[tone]}`}
    >
      {children}
    </span>
  )
}

/** "Board final {date}", red within 48 hours (§6.2). */
export function DeadlineChip({ at, label = 'Board final' }: { at: string; label?: string }) {
  const urgent = isUrgent(at)
  return (
    <Chip tone={urgent ? 'red' : 'grey'}>
      {label} {formatDateTime(at)}
      {urgent && <span className="sr-only"> (soon)</span>}
    </Chip>
  )
}

// ----------------------------------------------------------------- tabs and filter chips

export function StatusTabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
}: {
  label: string
  tabs: { id: T; label: string; count: number | null }[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="grid w-full max-w-[480px] gap-1 rounded-xl bg-[#e9edf3] p-1"
      style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={`min-h-11 rounded-[9px] px-1 text-sm font-semibold ${focusRing} ${
            value === tab.id
              ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
              : 'text-slate-600 hover:text-slate-900'
          }`}
        >
          {tab.count === null ? tab.label : `${tab.label} · ${tab.count}`}
        </button>
      ))}
    </div>
  )
}

export function FilterChip({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${focusRing} ${
        pressed
          ? 'border-brand-700 bg-brand-50 text-brand-900'
          : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
      }`}
    >
      {children}
    </button>
  )
}

// ----------------------------------------------------------------- dialogs

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)'

/**
 * Modal shell (the AdminDialog pattern, M3-DESIGN §6.3): `role="dialog"`, `aria-modal`, Esc
 * and a click outside dismiss, Tab stays inside (inputs and selects included), focus returns to
 * the opener. `wide` fits the per-deed choices of Confirm; long bodies scroll inside.
 */
export function FraudDialog({
  title,
  children,
  onDismiss,
  busy,
  wide = false,
}: {
  title: string
  children: (ids: { bodyId: string }) => ReactNode
  onDismiss: () => void
  busy: boolean
  wide?: boolean
}) {
  const titleId = useId()
  const bodyId = useId()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('[data-autofocus], select, textarea, input')?.focus()
    return () => opener?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      if (!busy) onDismiss()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = ref.current?.querySelectorAll<HTMLElement>(FOCUSABLE)
    if (!focusable || focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-slate-900/50 p-4 sm:items-center"
      onClick={busy ? undefined : onDismiss}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        className={`flex max-h-[calc(100dvh-2rem)] w-full flex-col gap-3 overflow-y-auto rounded-2xl bg-white p-5.5 shadow-xl ${
          wide ? 'max-w-[640px]' : 'max-w-[440px]'
        }`}
      >
        <h2 id={titleId} className="text-xl font-bold text-slate-900">
          {title}
        </h2>
        {children({ bodyId })}
      </div>
    </div>
  )
}

export function DialogButtons({
  confirm,
  danger = false,
  busy,
  onConfirm,
  onCancel,
}: {
  confirm: string
  danger?: boolean
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div className="mt-1.5 flex flex-col gap-2">
      <button
        type="button"
        onClick={onConfirm}
        disabled={busy}
        className={`min-h-12 w-full rounded-lg text-base font-semibold text-white disabled:opacity-60 ${focusRing} ${
          danger ? 'bg-red-700 hover:bg-red-800' : 'bg-brand-700 hover:bg-brand-900'
        }`}
      >
        {confirm}
      </button>
      <button
        type="button"
        onClick={onCancel}
        disabled={busy}
        className={`min-h-12 w-full rounded-lg border border-slate-200 bg-white text-base font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60 ${focusRing}`}
      >
        Cancel
      </button>
    </div>
  )
}

/** "Note for the record (required)" with a character counter; checks the minimum length. */
export function NoteField({
  note,
  onNote,
  options,
  invalid,
  placeholder = 'Why you decided this',
}: {
  note: string
  onNote: (note: string) => void
  options: FraudOptions | undefined
  invalid: boolean
  placeholder?: string
}) {
  const id = useId()
  const max = options?.note_max_chars ?? DEFAULT_NOTE_MAX
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[15px] font-semibold">
        Note for the record <span className="font-normal text-slate-600">(required)</span>
      </label>
      <textarea
        id={id}
        rows={3}
        value={note}
        maxLength={max}
        onChange={(event) => onNote(event.target.value)}
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        aria-describedby={`${id}-count`}
        className={fieldClass}
      />
      <p id={`${id}-count`} className="self-end text-xs text-slate-600">
        {note.length} / {max}
      </p>
    </div>
  )
}

/** A dialog with a body, a required note and one action (park, unpark, dismiss, appeals). */
export function NoteDialog({
  title,
  body,
  confirmLabel,
  busyLabel,
  danger = false,
  options,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  title: string
  body: ReactNode
  confirmLabel: string
  busyLabel: string
  danger?: boolean
  options: FraudOptions | undefined
  busy: boolean
  error: string | null
  onConfirm: (note: string) => void
  onCancel: () => void
}) {
  const field = useNote(options)
  return (
    <FraudDialog title={title} onDismiss={onCancel} busy={busy}>
      {({ bodyId }) => (
        <>
          <div id={bodyId} className="flex flex-col gap-2 text-slate-700">
            {body}
          </div>
          <NoteField
            note={field.note}
            onNote={field.setNote}
            options={options}
            invalid={field.invalid}
          />
          {(field.invalid || error) && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {field.invalid ? field.tooShortMessage : error}
            </p>
          )}
          <DialogButtons
            confirm={busy ? busyLabel : confirmLabel}
            danger={danger}
            busy={busy}
            onConfirm={() => {
              const text = field.check()
              if (text !== null) onConfirm(text)
            }}
            onCancel={onCancel}
          />
        </>
      )}
    </FraudDialog>
  )
}

// ----------------------------------------------------------------- misc

export const secondaryButtonClass = `inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 text-[15px] font-semibold text-slate-800 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 ${focusRing}`

export const linkClass = `rounded font-semibold text-brand-700 underline-offset-2 hover:text-brand-900 hover:underline ${focusRing}`

export function EmptyState({ title, body }: { title: string; body?: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-10 text-center">
      <h2 className="text-lg font-bold text-slate-900">{title}</h2>
      {body && <p className="text-slate-600">{body}</p>}
    </div>
  )
}
