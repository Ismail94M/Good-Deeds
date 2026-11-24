import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

const variants = {
  primary: 'border-0 bg-brand-700 text-white shadow-sm hover:bg-brand-900',
  danger: 'border-0 bg-red-700 text-white hover:bg-red-800',
  secondary: 'border border-slate-200 bg-white text-slate-800 hover:bg-slate-50',
}

export type ConfirmAction = {
  label: string
  onClick: () => void
  variant: keyof typeof variants
  /** Focused when the dialog opens: the safe choice. */
  autoFocus?: boolean
}

/**
 * Modal confirmation (M2-DESIGN §5.1: `role="dialog"`, `aria-modal`, closes on Esc and returns
 * focus to the opener). Buttons are full width and stacked, as on the F3 and F6 canvases.
 * Tab stays inside; Esc and a click outside call `onDismiss`.
 */
export function ConfirmDialog({
  title,
  body,
  actions,
  onDismiss,
  busy = false,
  error,
}: {
  title: string
  body: ReactNode
  actions: ConfirmAction[]
  onDismiss: () => void
  busy?: boolean
  error?: string | null
}) {
  const titleId = useId()
  const bodyId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const first =
      dialogRef.current?.querySelector<HTMLElement>('[data-autofocus]') ??
      dialogRef.current?.querySelector<HTMLElement>('button')
    first?.focus()
    return () => opener?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onDismiss()
      return
    }
    if (event.key !== 'Tab') return
    const buttons = dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled)')
    if (!buttons || buttons.length === 0) return
    const first = buttons[0]
    const last = buttons[buttons.length - 1]
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
        ref={dialogRef}
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
        <p id={bodyId} className="text-slate-700">
          {body}
        </p>
        {error && (
          <p role="alert" className="text-sm font-medium text-red-700">
            {error}
          </p>
        )}
        <div className="mt-1.5 flex flex-col gap-2">
          {actions.map((action, index) => (
            <button
              key={index}
              type="button"
              onClick={action.onClick}
              disabled={busy}
              data-autofocus={action.autoFocus ? '' : undefined}
              className={`inline-flex min-h-12 w-full items-center justify-center rounded-lg px-5 text-base font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${focusRing} ${variants[action.variant]}`}
            >
              {action.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
