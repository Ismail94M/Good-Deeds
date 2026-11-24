/** Layout and small parts shared by the F6 screens (My deeds, self-logs). */

import type { ReactNode } from 'react'
import { Link } from 'react-router'
import type { DeedStatus } from './deedsApi'
import { STATUS_LABELS } from './deedText'

export const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/** F6 column: 720 px on desktop, 16 px gutters on phones. */
export function F6Main({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex w-full max-w-[720px] flex-col gap-5 px-4 pt-5 pb-10 sm:px-6 sm:pt-10 sm:pb-14">
      {children}
    </main>
  )
}

export function F6Title({ children }: { children: ReactNode }) {
  return (
    <h1 className="text-[26px] leading-tight font-bold tracking-tight text-slate-900 sm:text-[32px]">
      {children}
    </h1>
  )
}

/** "‹ Profile" / "‹ My deeds" link above a screen's title. */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className={`-my-2 inline-flex min-h-11 items-center gap-1 self-start rounded-md pr-2 text-[15px] font-semibold text-brand-700 hover:text-brand-900 ${focusRing}`}
    >
      <ChevronLeft />
      {label}
    </Link>
  )
}

export function ChevronLeft() {
  return (
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
  )
}

const badgeTones: Record<DeedStatus, string> = {
  verified: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  withdrawn: 'border-slate-200 bg-slate-100 text-slate-600',
  pending: 'border-amber-200 bg-amber-50 text-amber-800',
  unverified: 'border-slate-200 bg-slate-100 text-slate-600',
  rejected: 'border-red-200 bg-red-50 text-red-700',
  removed: 'border-red-200 bg-red-50 text-red-700',
}

export function StatusBadge({ status }: { status: DeedStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[13px] font-semibold ${badgeTones[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  )
}

export function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-[13px] font-semibold text-slate-600">
      {children}
    </span>
  )
}

export function LockIcon({ className = 'size-4.5' }: { className?: string }) {
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
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

export function PlusIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-4.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

export function EditIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 20h4L19 9l-4-4L4 16z" />
    </svg>
  )
}

export const primaryButtonClass = `inline-flex min-h-12 items-center justify-center gap-1.5 rounded-lg bg-brand-700 px-5 text-base font-semibold text-white shadow-sm transition-colors hover:bg-brand-900 disabled:cursor-not-allowed disabled:opacity-60 ${focusRing}`
export const secondaryButtonClass = `inline-flex min-h-12 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-5 text-base font-semibold text-slate-800 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 ${focusRing}`
export const smallButtonClass = `inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[15px] font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`
export const smallDangerButtonClass = `inline-flex min-h-11 items-center justify-center rounded-lg border border-red-200 bg-white px-3 text-[15px] font-semibold text-red-700 hover:bg-red-50 ${focusRing}`

/** Dashed empty-state box (F6 canvas). */
export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon: ReactNode
  title: string
  body: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-slate-300 bg-white px-3 py-10 text-center">
      <span
        aria-hidden
        className="flex size-14 items-center justify-center rounded-full bg-brand-50 text-brand-700"
      >
        {icon}
      </span>
      <h2 className="text-lg font-bold text-slate-900">{title}</h2>
      <p className="max-w-[300px] text-slate-600">{body}</p>
      {action}
    </div>
  )
}

export function HeartIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-7"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 20.5s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.7a4.3 4.3 0 0 1 7.5 2.8c0 5.4-7.5 10-7.5 10z" />
    </svg>
  )
}
