/** Small parts shared by the feed screens (F7 canvas): icons, avatar, sheet, toast, chips. */

import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode, type SVGProps } from 'react'
import { Link } from 'react-router'
import { initials } from '../report/format'

export const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

// ----------------------------------------------------------------- icons

function Svg({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {children}
    </svg>
  )
}

export function HeartIcon({ filled }: { filled: boolean }) {
  return (
    <Svg
      className={`size-5.5 ${filled ? 'fill-rose-600 text-rose-600' : 'text-slate-700'}`}
      fill={filled ? 'currentColor' : 'none'}
    >
      <path d="M12 20.5s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.7a4.3 4.3 0 0 1 7.5 2.8c0 5.4-7.5 10-7.5 10z" />
    </Svg>
  )
}

export function CommentIcon() {
  return (
    <Svg className="size-5.5">
      <path d="M4 5h16v11H9l-5 4z" />
    </Svg>
  )
}

export function ShareIcon() {
  return (
    <Svg className="size-5.5">
      <path d="M12 4v11M7 9l5-5 5 5M5 14v5h14v-5" />
    </Svg>
  )
}

export function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <Svg className="size-5.5" fill={filled ? 'currentColor' : 'none'}>
      <path d="M6 4h12v17l-6-4-6 4z" />
    </Svg>
  )
}

export function DotsIcon() {
  return (
    <Svg className="size-5.5" strokeWidth="2.6">
      <path d="M6 12h.01M12 12h.01M18 12h.01" />
    </Svg>
  )
}

export function CheckIcon({ className = 'size-3.5' }: { className?: string }) {
  return (
    <Svg className={className} strokeWidth="2.8">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </Svg>
  )
}

export function EyeIcon({ className = 'size-5' }: { className?: string }) {
  return (
    <Svg className={className}>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </Svg>
  )
}

export function EyeOffIcon({ className = 'size-5' }: { className?: string }) {
  return (
    <Svg className={className}>
      <path d="M3 3l18 18M10.6 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1M6.6 6.6C3.9 8.4 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6" />
    </Svg>
  )
}

export function LinkIcon() {
  return (
    <Svg className="size-5">
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
    </Svg>
  )
}

export function WhatsAppIcon() {
  return (
    <Svg className="size-5">
      <path d="M20 12a8 8 0 0 1-11.8 7L4 20l1.1-4.1A8 8 0 1 1 20 12z" />
    </Svg>
  )
}

export function FlagIcon() {
  return (
    <Svg className="size-5">
      <path d="M5 21V4h11l-1.5 4L16 12H5" />
    </Svg>
  )
}

export function PeopleIcon() {
  return (
    <Svg className="size-7">
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
      <path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6.5 6.5 0 0 1 3.5 6" />
    </Svg>
  )
}

export function CircleCheckIcon() {
  return (
    <Svg className="size-7 text-emerald-700">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.8 2.8L16 10" />
    </Svg>
  )
}

export function ChevronIcon({ dir }: { dir: 'left' | 'right' }) {
  return (
    <Svg className="size-5">
      <path d={dir === 'left' ? 'M15 6l-6 6 6 6' : 'M9 6l6 6-6 6'} />
    </Svg>
  )
}

// ----------------------------------------------------------------- avatar

const AVATAR_TONES = [
  'bg-brand-100 text-brand-900',
  'bg-emerald-100 text-emerald-800',
  'bg-amber-200 text-amber-900',
  'bg-pink-100 text-pink-900',
  'bg-indigo-100 text-indigo-800',
]

/** A stable colour per handle, so the same person always looks the same. */
function toneFor(handle: string): string {
  let sum = 0
  for (const ch of handle) sum = (sum + ch.charCodeAt(0)) % 997
  return AVATAR_TONES[sum % AVATAR_TONES.length]
}

const AVATAR_SIZES = {
  sm: 'size-9 text-[13px]',
  md: 'size-10 text-sm',
  lg: 'size-11 text-[15px]',
  xl: 'size-22 text-[30px]',
}

/** Initials avatar; decorative (the name is always shown next to it). */
export function Avatar({
  name,
  handle,
  size = 'lg',
}: {
  name: string
  handle: string
  size?: keyof typeof AVATAR_SIZES
}) {
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full font-bold ${AVATAR_SIZES[size]} ${toneFor(handle)}`}
    >
      {initials(name)}
    </span>
  )
}

/** The avatar as a link to the person's profile. */
export function AvatarLink({ name, handle }: { name: string; handle: string }) {
  return (
    <Link
      to={`/u/${encodeURIComponent(handle)}`}
      aria-label={`${name}, open profile`}
      className={`shrink-0 rounded-full ${focusRing}`}
    >
      <Avatar name={name} handle={handle} />
    </Link>
  )
}

// ----------------------------------------------------------------- chips and pills

export function VerifiedChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700">
      <CheckIcon />
      Verified by a witness
    </span>
  )
}

export function RankChip({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-bold text-amber-800">
      {children}
    </span>
  )
}

/** Follow / Following pill (`aria-pressed`). */
export function FollowButton({
  following,
  onClick,
  name,
  large = false,
  disabled = false,
}: {
  following: boolean
  onClick: () => void
  /** Whose follow this is, for the accessible name. */
  name: string
  large?: boolean
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      aria-pressed={following}
      aria-label={`${following ? 'Following' : 'Follow'} ${name}`}
      onClick={onClick}
      disabled={disabled}
      className={`shrink-0 rounded-full border font-semibold transition-colors disabled:opacity-60 ${focusRing} ${
        large ? 'min-h-12 min-w-40 px-6 text-base' : 'min-h-11 px-3.5 text-sm'
      } ${
        following
          ? 'border-slate-300 bg-white text-slate-800 hover:bg-slate-50'
          : 'border-brand-700 bg-brand-700 text-white hover:bg-brand-900'
      }`}
    >
      {following ? 'Following' : 'Follow'}
    </button>
  )
}

// ----------------------------------------------------------------- segmented tabs

export function SegmentedTabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  panelId,
}: {
  label: string
  tabs: { id: T; label: string }[]
  value: T
  onChange: (id: T) => void
  panelId?: string
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const onKeyDown = (event: KeyboardEvent, index: number) => {
    let next = -1
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = tabs.length - 1
    if (next < 0) return
    event.preventDefault()
    refs.current[next]?.focus()
    onChange(tabs[next].id)
  }
  return (
    <div
      role="tablist"
      aria-label={label}
      className="grid gap-1 rounded-xl bg-[#e9edf3] p-1"
      style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
    >
      {tabs.map((tab, index) => {
        const selected = tab.id === value
        return (
          <button
            key={tab.id}
            ref={(el) => {
              refs.current[index] = el
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={`min-h-11 rounded-[9px] text-sm font-semibold ${focusRing} ${
              selected ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600'
            }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

// ----------------------------------------------------------------- sheet

/**
 * Bottom sheet on phones, centred card on wider screens: `role="dialog"`, `aria-modal`, Esc and
 * a click outside close it, Tab stays inside, focus returns to the opener (§6.4).
 */
export function Sheet({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string
  subtitle?: ReactNode
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('a[href], button:not(:disabled)')?.focus()
    return () => opener?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab') return
    const items = ref.current?.querySelectorAll<HTMLElement>(
      'a[href], button:not(:disabled), input, textarea',
    )
    if (!items || items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
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
      className="fixed inset-0 z-40 flex items-end justify-center bg-slate-900/50 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[90svh] w-full flex-col gap-2.5 overflow-y-auto rounded-t-2xl bg-white px-4 pt-3 pb-5 shadow-2xl sm:max-w-[440px] sm:rounded-2xl"
      >
        <span aria-hidden className="h-1 w-10 self-center rounded-full bg-slate-300 sm:hidden" />
        <h2 id={titleId} className="mt-1 text-lg font-bold text-slate-900">
          {title}
        </h2>
        {subtitle && <p className="-mt-1.5 text-sm text-slate-600">{subtitle}</p>}
        {children}
      </div>
    </div>
  )
}

const sheetRowClass = `flex min-h-13 w-full items-center gap-3.5 rounded-[10px] px-2 text-left text-base font-semibold text-slate-900 hover:bg-slate-50 ${focusRing}`

export function SheetRowIcon({ tone, children }: { tone: string; children: ReactNode }) {
  return (
    <span aria-hidden className={`flex size-10 items-center justify-center rounded-full ${tone}`}>
      {children}
    </span>
  )
}

export function SheetButton({
  icon,
  children,
  onClick,
}: {
  icon: ReactNode
  children: ReactNode
  onClick: () => void
}) {
  return (
    <button type="button" onClick={onClick} className={sheetRowClass}>
      {icon}
      {children}
    </button>
  )
}

export function SheetLink({
  icon,
  children,
  href,
  onClick,
}: {
  icon: ReactNode
  children: ReactNode
  href: string
  onClick: () => void
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={onClick}
      className={sheetRowClass}
    >
      {icon}
      {children}
    </a>
  )
}

export function SheetCancel({
  onClick,
  label = 'Cancel',
}: {
  onClick: () => void
  label?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-h-12 rounded-lg border border-slate-200 bg-white text-base font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`}
    >
      {label}
    </button>
  )
}

// ----------------------------------------------------------------- layout

/** The feed column: 640 px wide on desktop, 16 px gutters on phones. */
export function FeedMain({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <main
      aria-label={label}
      className="mx-auto flex w-full max-w-[640px] flex-col gap-4 px-4 pt-4 pb-10 sm:px-6 sm:pt-8 sm:pb-14"
    >
      {children}
    </main>
  )
}

/** Dashed empty-state box (F7 canvas). */
export function EmptyBox({
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
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-10 text-center">
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

export const primaryButton = `inline-flex min-h-12 items-center justify-center rounded-lg bg-brand-700 px-5 text-base font-semibold text-white shadow-sm hover:bg-brand-900 disabled:cursor-not-allowed disabled:opacity-60 ${focusRing}`
export const secondaryButton = `inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-200 bg-white px-4 font-semibold text-slate-800 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 ${focusRing}`
