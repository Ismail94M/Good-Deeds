import { Link } from 'react-router'
import type { ErrorActionKind, ErrorCopy } from './errorCopy'
import { AlertIcon, primaryButton, ReportMain, ScreenTitle, secondaryButton } from './reportUi'

const ROUTES: Record<Exclude<ErrorActionKind, 'retry'>, string> = {
  scan_again: '/report/scan',
  record_hub: '/report',
  home: '/',
  my_reports: '/me/deeds?tab=reported',
}

/**
 * The full-screen error layout (F3 canvas, row 5): icon, title, body, the copy's primary action
 * and "Back to Home". `onRetry` handles the `retry` action (and `scan_again` when given
 * `onScanAgain`, e.g. on the camera page itself).
 */
export function ReportErrorView({
  copy,
  onRetry,
  onScanAgain,
}: {
  copy: ErrorCopy
  onRetry?: () => void
  onScanAgain?: () => void
}) {
  const tone = copy.tone === 'info' ? 'bg-brand-50 text-brand-900' : 'bg-amber-50 text-amber-700'
  const action = copy.action
  let primary = null
  if (action) {
    const handler =
      action.kind === 'retry' ? onRetry : action.kind === 'scan_again' ? onScanAgain : undefined
    if (handler) {
      primary = (
        <button type="button" onClick={handler} className={primaryButton}>
          {action.label}
        </button>
      )
    } else if (action.kind !== 'retry') {
      primary = (
        <Link to={ROUTES[action.kind]} className={primaryButton}>
          {action.label}
        </Link>
      )
    }
  }
  const showHome = action?.kind !== 'home'

  return (
    <ReportMain>
      <div className="flex flex-col items-start gap-4 pt-6">
        <span
          aria-hidden
          className={`flex size-15 items-center justify-center rounded-full ${tone}`}
        >
          <AlertIcon className="size-7.5" />
        </span>
        <ScreenTitle autoFocus>{copy.title}</ScreenTitle>
        {copy.body && <p className="text-slate-700">{copy.body}</p>}
        <div className="mt-2 flex w-full flex-col gap-2.5">
          {primary}
          {showHome && (
            <Link to="/" className={secondaryButton}>
              Back to Home
            </Link>
          )}
        </div>
      </div>
    </ReportMain>
  )
}
