import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { focusRing } from './adminUi'

export const fieldClass = `w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-[15px] text-slate-900 ${focusRing}`

/** Notes are 5..`decision_note_max` characters (§3.3 #13). */
export const NOTE_MIN = 5
export const NOTE_TOO_SHORT = 'Write a short note (at least 5 characters) before you decide.'

/**
 * The dialog's Tab stops in order. A radio group is one stop: its checked radio (or its first
 * radio when none is checked), as browsers tab through radios.
 */
function tabStops(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  const all = root.querySelectorAll<HTMLElement>(
    'button:not(:disabled), select, textarea, input[type="radio"]:not(:disabled)',
  )
  // Sorted into document order: some engines (jsdom) return a selector list group by group.
  const ordered = Array.from(all).sort((a, b) =>
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
  )
  return ordered.filter((el) => {
    if (!(el instanceof HTMLInputElement) || el.type !== 'radio') return true
    const group = Array.from(
      root.querySelectorAll<HTMLInputElement>('input[type="radio"]:not(:disabled)'),
    ).filter((r) => r.name === el.name)
    return el.checked || (!group.some((r) => r.checked) && group[0] === el)
  })
}

/**
 * Modal shell for the admin dialogs: `role="dialog"`, `aria-modal`, Esc and a click outside
 * dismiss, Tab stays inside, focus returns to the opener.
 */
export function AdminDialog({
  title,
  children,
  onDismiss,
  busy,
}: {
  title: string
  children: (ids: { titleId: string; bodyId: string }) => ReactNode
  onDismiss: () => void
  busy: boolean
}) {
  const titleId = useId()
  const bodyId = useId()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('[data-autofocus], select, textarea, button')?.focus()
    return () => opener?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      if (!busy) onDismiss()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = tabStops(ref.current)
    if (focusable.length === 0) return
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
        className="flex w-full max-w-[440px] flex-col gap-3 rounded-2xl bg-white p-5.5 shadow-xl"
      >
        <h2 id={titleId} className="text-xl font-bold text-slate-900">
          {title}
        </h2>
        {children({ titleId, bodyId })}
      </div>
    </div>
  )
}

export function DialogActions({
  confirm,
  danger,
  busy,
  onConfirm,
  onCancel,
}: {
  confirm: string
  danger: boolean
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

/** Restore asks for its own note (_interim_, §6.3 "Done banner"); photos and comments. */
export function RestoreDialog({
  title,
  body,
  confirmLabel,
  noteMax,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  title: string
  body: string
  confirmLabel: string
  noteMax: number
  busy: boolean
  error: string | null
  onConfirm: (note: string) => void
  onCancel: () => void
}) {
  const noteId = useId()
  const [note, setNote] = useState('')
  const [tooShort, setTooShort] = useState(false)
  return (
    <AdminDialog title={title} onDismiss={onCancel} busy={busy}>
      {({ bodyId }) => (
        <>
          <p id={bodyId} className="text-slate-700">
            {body}
          </p>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={noteId} className="text-[15px] font-semibold">
              Note for the record <span className="font-normal text-slate-600">(required)</span>
            </label>
            <textarea
              id={noteId}
              rows={3}
              value={note}
              maxLength={noteMax}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Why you’re restoring it"
              aria-invalid={tooShort || undefined}
              className={fieldClass}
            />
          </div>
          {(tooShort || error) && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {tooShort ? NOTE_TOO_SHORT : error}
            </p>
          )}
          <DialogActions
            confirm={busy ? 'Restoring…' : confirmLabel}
            danger={false}
            busy={busy}
            onConfirm={() => {
              if (note.trim().length < NOTE_MIN) {
                setTooShort(true)
                return
              }
              setTooShort(false)
              onConfirm(note.trim())
            }}
            onCancel={onCancel}
          />
        </>
      )}
    </AdminDialog>
  )
}
