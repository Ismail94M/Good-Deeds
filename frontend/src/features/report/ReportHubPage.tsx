import { useQuery } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { Loading } from '../../components/ui'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'
import { copyFor } from './errorCopy'
import { totalLimitNotice, totalLimitState } from './limits'
import { ReportErrorView } from './ReportErrorView'
import { clearRescanNotice, hasRescanNotice } from './rescanNotice'
import { Banner, focusRing, InfoIcon, LimitBanner, ReportMain, ScreenTitle } from './reportUi'

/**
 * `/report`, the Record a deed hub (F3 canvas row 1): Scan their QR, Enter ID and code, the
 * no-invite guidance line (D26), How it works, and the monthly total banner (D20).
 */
export function ReportHubPage() {
  const navigate = useNavigate()
  const options = useQuery({ queryKey: deedsKeys.options, queryFn: deedsApi.options })
  // After logging in from a scanned link: its code has expired by now (§5.3 Q6).
  const [rescan] = useState(hasRescanNotice)
  useEffect(() => {
    if (rescan) clearRescanNotice()
  }, [rescan])

  if (options.isPending) return <Loading />
  if (options.isError) {
    return <ReportErrorView copy={copyFor('generic')} onRetry={() => void options.refetch()} />
  }

  const notice = totalLimitNotice(options.data)
  const full =
    totalLimitState(
      options.data.reports_remaining_this_month,
      options.data.reporter_monthly_warn_at,
    ) === 'full'

  return (
    <ReportMain wide>
      <div className="flex flex-col gap-2">
        <ScreenTitle>Record a deed</ScreenTitle>
        <p className="text-slate-600">
          Saw someone do something good? Recognise them while you’re both still there.
        </p>
      </div>
      {rescan && (
        <Banner tone="info" role="status">
          <strong>{copyFor('qr_invalid').title}.</strong> {copyFor('qr_invalid').body}
        </Banner>
      )}
      {notice && <LimitBanner notice={notice} />}
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
        <OptionCard
          title="Scan their QR"
          body="Fastest. Point your camera at the code on their phone."
          disabled={full}
          onClick={() => navigate('/report/scan')}
          icon={
            <span className="flex size-13 shrink-0 items-center justify-center rounded-[14px] bg-brand-700 text-white">
              <svg
                viewBox="0 0 24 24"
                className="size-6.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" />
                <path d="M7 12h10" />
              </svg>
            </span>
          }
        />
        <OptionCard
          title="Enter ID and code"
          body="If scanning doesn’t work, they read out their ID and 6-digit code."
          disabled={full}
          onClick={() => navigate('/report/code')}
          icon={
            <span className="flex size-13 shrink-0 items-center justify-center rounded-[14px] bg-brand-50 text-brand-700">
              <svg
                viewBox="0 0 24 24"
                className="size-6.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="3" y="6" width="18" height="12" rx="2" />
                <path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10" />
              </svg>
            </span>
          }
        />
      </div>
      <p className="flex items-start gap-2.5 text-[15px] text-slate-600">
        <InfoIcon />
        <span>Doer not on Good Deeds? Ask them to sign up, then show you their QR.</span>
      </p>
      <section
        aria-labelledby="how-it-works"
        className="flex flex-col gap-3 border-t border-slate-200 pt-4.5"
      >
        <h2
          id="how-it-works"
          className="text-[13px] font-semibold tracking-[0.06em] text-slate-600 uppercase"
        >
          How it works
        </h2>
        <ol className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {[
            'Find them: scan their QR or type their ID and code.',
            'We check you’re both in the same place.',
            'Say what they did. It’s verified straight away.',
          ].map((step, index) => (
            <li key={step} className="flex items-start gap-3">
              <span
                aria-hidden
                className="flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-50 text-sm font-bold text-brand-900"
              >
                {index + 1}
              </span>
              <span className="text-[15px]">{step}</span>
            </li>
          ))}
        </ol>
      </section>
    </ReportMain>
  )
}

function OptionCard({
  title,
  body,
  icon,
  disabled,
  onClick,
}: {
  title: string
  body: string
  icon: ReactNode
  disabled: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      className={`flex w-full items-center gap-4 rounded-2xl border border-slate-200 bg-white p-5 text-left text-slate-900 shadow-sm ${focusRing} ${
        disabled ? 'cursor-not-allowed opacity-50' : 'hover:border-brand-100 hover:bg-brand-50/40'
      }`}
    >
      <span aria-hidden>{icon}</span>
      <span className="min-w-0 grow">
        <span className="block text-[17px] font-bold">{title}</span>
        <span className="block text-sm text-slate-600">{body}</span>
      </span>
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        className="size-5 shrink-0"
        fill="none"
        stroke="#586579"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M9 6l6 6-6 6" />
      </svg>
    </button>
  )
}
