import { useEffect, useRef, type KeyboardEvent } from 'react'
import type { QrCode } from '../auth/identityApi'
import { CountdownBar } from './CountdownBar'
import { QrImage } from './QrImage'
import { formatManualCode } from './qrFormat'

/**
 * "Show large QR" (F2 canvas): full screen, laid out for scanning from about 2 m. Close or Esc
 * returns, and focus goes back to the button that opened it.
 */
export function LargeQrDialog({
  qr,
  displayName,
  secondsLeft,
  windowSeconds,
  reconnecting = false,
  onClose,
}: {
  qr: QrCode
  displayName: string
  secondsLeft: number
  windowSeconds: number
  /** A refresh failed: show the last code greyed, with "Reconnecting…" (as on the page). */
  reconnecting?: boolean
  onClose: () => void
}) {
  const dim = reconnecting ? 'opacity-55 grayscale' : ''
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    return () => opener?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
    } else if (event.key === 'Tab') {
      // The Close button is the only control: keep focus on it.
      event.preventDefault()
      closeRef.current?.focus()
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Large QR code"
      onKeyDown={onKeyDown}
      className="fixed inset-0 z-50 flex flex-col overflow-y-auto bg-white"
    >
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-2.5">
        <p className="min-w-0 truncate text-[15px] font-semibold text-slate-600">
          {displayName} · My QR
        </p>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-[15px] font-semibold text-slate-800 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700"
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
          Close<span className="hidden sm:inline"> (Esc)</span>
        </button>
      </div>
      <div className="flex grow flex-col items-center justify-center gap-6 px-4 pt-6 pb-8 lg:flex-row lg:gap-18">
        <div className={`shrink-0 ${dim}`} data-testid="large-qr-frame">
          <QrImage
            value={qr.scan_url}
            label={`QR code for ${qr.display_id}`}
            className="w-[290px] max-w-full rounded-2xl border-2 border-slate-900 p-3.5 lg:w-[520px] lg:p-7"
          />
        </div>
        <div className="flex w-[340px] max-w-full flex-col items-center gap-4 text-center lg:w-[460px] lg:gap-7">
          <div className={dim}>
            <p className="text-sm font-semibold tracking-[0.08em] text-slate-600 uppercase">ID</p>
            <p className="font-mono text-[30px] leading-tight font-bold tracking-[0.04em] text-slate-900 lg:text-5xl">
              {qr.display_id}
            </p>
          </div>
          <div className={dim}>
            <p className="text-sm font-semibold tracking-[0.08em] text-slate-600 uppercase">Code</p>
            {/* Not copyable on purpose, as on the page. */}
            <p
              className="font-mono text-[56px] leading-[1.1] font-bold tracking-[0.06em] text-slate-900 select-none lg:text-8xl"
              onCopy={(event) => event.preventDefault()}
            >
              {formatManualCode(qr.manual_code)}
            </p>
          </div>
          {reconnecting ? (
            <div
              role="status"
              className="w-full rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-amber-800"
            >
              <p className="font-semibold">
                {'Reconnecting… You can keep showing this code for now.'}
              </p>
            </div>
          ) : (
            <CountdownBar secondsLeft={secondsLeft} windowSeconds={windowSeconds} size="large" />
          )}
        </div>
      </div>
    </div>
  )
}
