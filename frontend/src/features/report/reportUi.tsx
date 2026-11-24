/** Layout and small visual parts shared by the Record a deed screens (F3 canvas). */

import { useEffect, useRef, type ReactNode } from 'react'
import type { LimitNotice } from './limits'

export const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/** Page column: 600 px wide (880 px on the hub), 16 px gutters on phones. */
export function ReportMain({ wide = false, children }: { wide?: boolean; children: ReactNode }) {
  return (
    <main
      className={`mx-auto flex w-full ${
        wide ? 'max-w-[880px]' : 'max-w-[600px]'
      } flex-col gap-[22px] px-4 pt-5 pb-10 sm:px-6 sm:pt-10 sm:pb-14`}
    >
      {children}
    </main>
  )
}

/**
 * Screen title. `autoFocus` moves focus to it when the screen replaces another one (errors,
 * results), so screen readers announce the new screen.
 */
export function ScreenTitle({
  children,
  autoFocus = false,
}: {
  children: ReactNode
  autoFocus?: boolean
}) {
  const ref = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])
  return (
    <h1
      ref={ref}
      tabIndex={autoFocus ? -1 : undefined}
      className="text-[26px] leading-tight font-bold tracking-tight text-slate-900 outline-none sm:text-[32px]"
    >
      {children}
    </h1>
  )
}

/** "‹ Back" / "‹ Cancel" link-button above a screen's title. */
export function BackButton({ label = 'Back', onClick }: { label?: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-my-2 inline-flex min-h-11 items-center gap-1 self-start rounded-md pr-2 text-[15px] font-semibold text-brand-700 hover:text-brand-900 ${focusRing}`}
    >
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
        <path d="M15 6l-6 6 6 6" />
      </svg>
      {label}
    </button>
  )
}

/** Input border as drawn (slate-300). */
export const inputBorder = 'border border-slate-300'

/** Full-width primary / secondary buttons as drawn (48 px tall). */
export const primaryButton = `inline-flex min-h-12 w-full items-center justify-center rounded-lg bg-brand-700 px-5 text-base font-semibold text-white shadow-sm transition-colors hover:bg-brand-900 disabled:cursor-not-allowed disabled:opacity-60 ${focusRing}`
export const secondaryButton = `inline-flex min-h-12 w-full items-center justify-center rounded-lg border border-slate-200 bg-white px-5 text-base font-semibold text-slate-800 transition-colors hover:bg-slate-50 ${focusRing}`

export function InfoIcon({ className = 'mt-0.5 size-5 shrink-0' }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </svg>
  )
}

export function AlertIcon({ className = 'mt-0.5 size-5 shrink-0' }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5.5M12 16.5h.01" />
    </svg>
  )
}

export function WarningIcon({ className = 'mt-0.5 size-5 shrink-0' }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
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

const bannerTones = {
  info: 'border border-indigo-200 bg-brand-50 text-brand-900',
  warning: 'border border-amber-200 bg-amber-50 text-amber-800',
  danger: 'border border-red-200 bg-red-50 text-red-800',
}

/** Rounded message box (blue info, amber warning, red error) with an icon. */
export function Banner({
  tone,
  role,
  icon,
  children,
}: {
  tone: keyof typeof bannerTones
  role?: 'status' | 'alert'
  icon?: ReactNode
  children: ReactNode
}) {
  return (
    <div
      role={role}
      className={`flex items-start gap-2.5 rounded-xl px-3.5 py-3 text-[15px] ${bannerTones[tone]}`}
    >
      {icon ?? <InfoIcon />}
      <div className="min-w-0">{children}</div>
    </div>
  )
}

/**
 * Monthly-limit banner (D20): the hub's total warning / maximum, and the deed form's pair
 * warning. A blocking notice is an alert with the warning triangle.
 */
export function LimitBanner({ notice }: { notice: LimitNotice }) {
  return (
    <Banner
      tone={notice.tone}
      role={notice.blocking ? 'alert' : 'status'}
      icon={notice.blocking ? <WarningIcon /> : <InfoIcon />}
    >
      <strong>{notice.lead}</strong> {notice.rest}
    </Banner>
  )
}

/** Green "Location ready · ±35 m" chip. `dark` for the camera screen. */
export function LocationChip({ accuracyM, dark = false }: { accuracyM: number; dark?: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-2 self-start rounded-full px-3 py-1.5 text-[13px] font-semibold ${
        dark ? 'bg-emerald-400/15 text-emerald-200' : 'bg-emerald-50 text-emerald-800'
      }`}
    >
      <span
        aria-hidden
        className={`size-2 rounded-full ${dark ? 'bg-emerald-400' : 'bg-emerald-600'}`}
      />
      Location ready · ±{accuracyM} m
    </span>
  )
}

/** Numbered steps with filled blue circles (location help, camera-app steps). */
export function NumberedSteps({ steps }: { steps: ReactNode[] }) {
  return (
    <ol className="flex flex-col gap-3">
      {steps.map((step, index) => (
        <li key={index} className="flex items-start gap-3">
          <span
            aria-hidden
            className="flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-700 text-sm font-bold text-white"
          >
            {index + 1}
          </span>
          <span>{step}</span>
        </li>
      ))}
    </ol>
  )
}

export function CheckIcon({ className = 'size-[22px] shrink-0' }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  )
}
