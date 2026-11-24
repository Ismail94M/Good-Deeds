import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ErrorAlert } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useCountdown } from '../../lib/useCountdown'
import { useAuth } from '../auth/authContext'
import { identityApi, queryKeys, type QrCode } from '../auth/identityApi'
import { CountdownBar } from './CountdownBar'
import { LargeQrDialog } from './LargeQrDialog'
import { QrImage } from './QrImage'
import { DEFAULT_WINDOW_SECONDS, formatManualCode, PUBLIC_FEED_NOTICE } from './qrFormat'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'
const card = 'rounded-2xl border border-slate-200 bg-white p-5 shadow-sm'
const outlineButton = `inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60 ${focusRing}`
const primaryButton = `inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-brand-700 px-5 font-semibold text-white hover:bg-brand-900 ${focusRing}`

/** How long "Copied" and the "ID copied" toast stay (F2 canvas). */
const COPIED_MS = 2500

const refreshMs = (qr: QrCode | undefined) =>
  qr ? Math.max(1, qr.refresh_after_seconds) * 1000 : false

/**
 * `/me/qr` (F2 canvas, M2-DESIGN §5.3). The QR and the 6-digit code refresh when the server
 * says to. A failed refresh keeps the last code, greyed, with "Reconnecting…" until its
 * `expires_at`; after that, or on a failed first load, the error state. An expired code is
 * never shown as usable.
 */
export function MyQrPage() {
  const { user } = useAuth()
  const query = useQuery({
    queryKey: queryKeys.qr,
    queryFn: identityApi.qr,
    // The token changes every window: refetch exactly when the server says to, and keep
    // retrying on that schedule after a failure.
    refetchInterval: (q) => refreshMs(q.state.data),
    staleTime: (q) => refreshMs(q.state.data) || 0,
    retry: false,
  })
  const { data, error, isFetching, dataUpdatedAt } = query

  const windowSeconds = data?.window_seconds || DEFAULT_WINDOW_SECONDS
  const secondsLeft = useCountdown(data ? dataUpdatedAt + data.refresh_after_seconds * 1000 : null)
  const secondsToExpiry = useCountdown(data ? Date.parse(data.expires_at) : null)
  const [large, setLarge] = useState(false)

  let body: ReactNode
  if (!data && !error) {
    body = <LoadingState />
  } else if (!data && error instanceof ApiError && error.code === 'onboarding_required') {
    body = <GatedState />
  } else if (data && secondsToExpiry > 0) {
    body = (
      <ReadyState
        qr={data}
        reconnecting={!!error}
        secondsLeft={secondsLeft}
        windowSeconds={windowSeconds}
        onOpenLarge={() => setLarge(true)}
      />
    )
  } else if (data && !error && isFetching) {
    // The code ran out while a refresh is on its way (e.g. the phone slept).
    body = <LoadingState />
  } else {
    body = <ErrorState retrying={isFetching} onRetry={() => void query.refetch()} />
  }

  return (
    <main className="mx-auto flex w-full max-w-[1088px] flex-col gap-6 px-4 pt-6 pb-10 sm:px-6 lg:pt-10 lg:pb-14">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl leading-tight font-bold tracking-tight text-slate-900 lg:text-[30px]">
          My QR code
        </h1>
        <p className="max-w-[560px] text-slate-600">
          Show this to someone who saw your good deed. They scan it to recognise you.
        </p>
      </div>
      {body}
      {large && data && secondsToExpiry > 0 && (
        <LargeQrDialog
          qr={data}
          displayName={user?.display_name ?? ''}
          secondsLeft={secondsLeft}
          windowSeconds={windowSeconds}
          reconnecting={!!error}
          onClose={() => setLarge(false)}
        />
      )}
    </main>
  )
}

function LoadingState() {
  return (
    <div role="status" aria-live="polite" className="flex max-w-[420px] flex-col gap-5">
      <div className={`${card} flex flex-col items-center gap-4`}>
        <div className="aspect-square w-full max-w-[264px] rounded-xl bg-slate-100 lg:max-w-[340px]" />
        <div className="h-2 w-full rounded-full bg-slate-100" />
        <p className="text-[15px] text-slate-600">Loading your QR code…</p>
      </div>
    </div>
  )
}

function GatedState() {
  return (
    <div className="flex max-w-[560px] flex-col gap-4">
      <div
        role="alert"
        className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3.5 text-amber-800"
      >
        <WarningIcon />
        <div>
          <strong className="block">Finish setting up to get your QR code.</strong>
          People can record your deeds once you’ve confirmed you’re 18 or over, accepted the terms
          and verified your phone number.
        </div>
      </div>
      <div>
        <Link to="/onboarding" className={primaryButton}>
          Finish setting up
        </Link>
      </div>
    </div>
  )
}

function ErrorState({ retrying, onRetry }: { retrying: boolean; onRetry: () => void }) {
  return (
    <div className="flex max-w-[560px] flex-col gap-4">
      <div
        role="alert"
        className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3.5 text-red-800"
      >
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="mt-px size-5.5 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7.5v5.5M12 16.5h.01" />
        </svg>
        <div>
          <strong className="block">We couldn’t load your QR code.</strong>
          Check your internet connection and try again.
        </div>
      </div>
      <div className="flex h-[180px] w-[264px] max-w-full items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 p-4 text-center text-[15px] text-[#586579] lg:h-[220px] lg:w-[340px]">
        QR code unavailable
      </div>
      <div>
        <button type="button" onClick={onRetry} disabled={retrying} className={primaryButton}>
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />
          </svg>
          {retrying ? 'Trying again…' : 'Try again'}
        </button>
      </div>
    </div>
  )
}

function ReadyState({
  qr,
  reconnecting,
  secondsLeft,
  windowSeconds,
  onOpenLarge,
}: {
  qr: QrCode
  reconnecting: boolean
  secondsLeft: number
  windowSeconds: number
  onOpenLarge: () => void
}) {
  const dim = reconnecting ? 'opacity-55 grayscale' : ''
  return (
    <div className="flex flex-col gap-5 lg:grid lg:grid-cols-[420px_minmax(0,1fr)] lg:items-start lg:gap-8">
      <div className="flex flex-col gap-4">
        <section aria-label="Your QR code" className={`${card} flex flex-col items-center gap-4`}>
          {reconnecting && (
            <div
              role="status"
              className="flex w-full items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-[15px] text-amber-800"
            >
              <svg
                aria-hidden
                viewBox="0 0 24 24"
                className="mt-0.5 size-5 shrink-0"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <path d="M2 8.8a15 15 0 0 1 4.2-2.6M10.7 5.1A15 15 0 0 1 22 8.8M5 12.9a10 10 0 0 1 5.2-2.8M19 12.9a10 10 0 0 0-2.2-1.5M8.5 16.4a5 5 0 0 1 7 0M12 20h.01M3 3l18 18" />
              </svg>
              <div>
                <strong className="block">Reconnecting…</strong>You can keep showing this code for
                now.
              </div>
            </div>
          )}
          <div className={dim} data-testid="qr-frame">
            <QrImage
              value={qr.scan_url}
              label={`QR code for ${qr.display_id}`}
              className="w-[264px] max-w-full rounded-xl border border-slate-200 p-4 lg:w-[340px]"
            />
          </div>
          {!reconnecting && (
            <CountdownBar secondsLeft={secondsLeft} windowSeconds={windowSeconds} />
          )}
          <button type="button" onClick={onOpenLarge} className={`${outlineButton} w-full`}>
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              className="size-4.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
            </svg>
            Show large QR
          </button>
        </section>
        <FeedNotice />
      </div>

      <div className="flex min-w-0 flex-col gap-5">
        <IdAndCode qr={qr} dim={dim} windowSeconds={windowSeconds} />
        <section
          aria-labelledby="qr-how"
          className="flex flex-col gap-3.5 rounded-2xl bg-brand-50 p-5"
        >
          <h2 id="qr-how" className="font-semibold text-slate-900">
            How the person who saw your deed records it
          </h2>
          <ol className="flex flex-col gap-3">
            <Step n={1}>
              They open <strong>Record a deed</strong> in Good Deeds and scan your QR.
            </Step>
            <Step n={2}>
              No camera, or it won’t scan? They tap <strong>Enter ID and code</strong> and type the
              two above.
            </Step>
          </ol>
          <p className="text-sm text-slate-700">{PUBLIC_FEED_NOTICE}</p>
        </section>
        <ResetSection />
      </div>
    </div>
  )
}

/** Q21: under the QR, the doer is told deeds about them are public. */
function FeedNotice() {
  return (
    <p className="flex items-start gap-2.5 rounded-xl bg-white px-3.5 py-3 text-sm text-slate-700 ring-1 ring-slate-200">
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        className="mt-px size-5 shrink-0 text-brand-700"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
      <span>{PUBLIC_FEED_NOTICE}</span>
    </p>
  )
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className="flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-700 text-sm font-bold text-white"
      >
        {n}
      </span>
      <span className="text-slate-800">{children}</span>
    </li>
  )
}

function IdAndCode({ qr, dim, windowSeconds }: { qr: QrCode; dim: string; windowSeconds: number }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(qr.display_id)
    } catch {
      return
    }
    setCopied(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(false), COPIED_MS)
  }

  return (
    <section aria-labelledby="qr-idcode" className={`${card} flex flex-col gap-4`}>
      <h2
        id="qr-idcode"
        className="text-[13px] font-semibold tracking-[0.06em] text-slate-600 uppercase"
      >
        No camera? Give them your ID and code
      </h2>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className={dim}>
          <p className="text-sm font-medium text-slate-600">Your ID</p>
          <p className="font-mono text-[26px] leading-tight font-bold tracking-[0.04em] text-slate-900">
            {qr.display_id}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void copyId()}
          className={
            copied
              ? `inline-flex min-h-11 items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 text-[15px] font-semibold text-emerald-700 ${focusRing}`
              : `${outlineButton} text-[15px]`
          }
        >
          {copied ? (
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              className="size-4.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
          ) : (
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              className="size-4.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="9" y="9" width="11" height="11" rx="2" />
              <path d="M5 15V5a2 2 0 0 1 2-2h10" />
            </svg>
          )}
          {copied ? 'Copied' : 'Copy ID'}
        </button>
      </div>
      <div className="h-px bg-slate-200" />
      <div className={dim}>
        <p className="text-sm font-medium text-slate-600">Code</p>
        {/* Not copyable on purpose: the witness must read it in person. */}
        <p
          className="font-mono text-[40px] leading-tight font-bold tracking-[0.08em] text-slate-900 select-none"
          onCopy={(event) => event.preventDefault()}
        >
          {formatManualCode(qr.manual_code)}
        </p>
      </div>
      <p className="text-sm text-[#586579]">
        They type both. The code changes every {windowSeconds} s and can’t be copied, so read it out
        or let them look at your screen.
      </p>
      {copied && (
        <div
          role="status"
          className="fixed bottom-6 left-1/2 z-30 flex -translate-x-1/2 items-center gap-2.5 rounded-xl bg-slate-900 px-4.5 py-3 text-[15px] font-medium whitespace-nowrap text-white shadow-lg"
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-4.5"
            fill="none"
            stroke="#6ee7b7"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M5 12.5l4.5 4.5L19 7.5" />
          </svg>
          ID copied
        </div>
      )}
    </section>
  )
}

function ResetSection() {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [done, setDone] = useState(false)
  const rotate = useMutation({
    mutationFn: identityApi.rotateQr,
    onSuccess: (qr) => {
      queryClient.setQueryData(queryKeys.qr, qr)
      setConfirming(false)
      setDone(true)
    },
  })

  return (
    <section aria-labelledby="qr-reset" className={`${card} flex flex-col gap-3`}>
      <h2 id="qr-reset" className="font-semibold text-slate-900">
        Worried someone copied your QR?
      </h2>
      {done && (
        <div
          role="status"
          className="flex items-start gap-2.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3 text-[15px] text-emerald-800"
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="mt-0.5 size-5 shrink-0"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M8 12.5l2.8 2.8L16 10" />
          </svg>
          <span>Your QR code has been reset. Old screenshots no longer work.</span>
        </div>
      )}
      {rotate.error && <ErrorAlert>{errorMessage(rotate.error)}</ErrorAlert>}
      {confirming ? (
        <div className="flex flex-col gap-3">
          <p className="text-slate-800">
            Resetting makes every earlier copy of your QR code stop working. Your ID stays the same.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={rotate.isPending}
              onClick={() => rotate.mutate()}
              className={`inline-flex min-h-11 items-center justify-center rounded-lg bg-red-700 px-5 font-semibold text-white hover:bg-red-800 disabled:opacity-60 ${focusRing}`}
            >
              {rotate.isPending ? 'Resetting…' : 'Yes, reset my QR'}
            </button>
            <button type="button" onClick={() => setConfirming(false)} className={outlineButton}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-start gap-3">
          <p className="text-[15px] text-slate-600">
            Resetting makes old screenshots of your QR stop working.
          </p>
          <button
            type="button"
            onClick={() => {
              setDone(false)
              setConfirming(true)
            }}
            className={outlineButton}
          >
            Reset my QR
          </button>
        </div>
      )}
    </section>
  )
}

function WarningIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="mt-px size-5.5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3.5L2.5 20h19z" />
      <path d="M12 10v4.5M12 17.5h.01" />
    </svg>
  )
}
