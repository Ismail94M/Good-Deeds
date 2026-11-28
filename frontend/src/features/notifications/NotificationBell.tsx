import { NavLink } from 'react-router'
import { useUnreadCount } from './notificationsApi'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

export function BellIcon({ className = 'size-6' }: { className?: string }) {
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
      <path d="M6 9.5a6 6 0 0 1 12 0c0 4.6 1.8 6.4 2.5 7H3.5c.7-.6 2.5-2.4 2.5-7z" />
      <path d="M10 20a2.2 2.2 0 0 0 4 0" />
    </svg>
  )
}

/** "3" or "9+". */
export function badgeText(count: number): string {
  return count > 9 ? '9+' : String(count)
}

/**
 * The header bell (§6.3, D114): a red count of rows new since the list was last opened, polled
 * every minute while the tab is visible. Opens `/notifications`.
 */
export function NotificationBell() {
  const { data } = useUnreadCount()
  const count = data?.new_since_seen ?? 0
  const label = count > 0 ? `Notifications, ${count} new` : 'Notifications'
  return (
    <NavLink
      to="/notifications"
      aria-label={label}
      className={({ isActive }) =>
        `relative inline-flex size-11 min-h-11 shrink-0 items-center justify-center rounded-full ${focusRing} ${
          isActive ? 'bg-brand-50 text-brand-700' : 'text-slate-700 hover:bg-slate-100'
        }`
      }
    >
      <BellIcon />
      {count > 0 && (
        <span
          aria-hidden
          data-testid="bell-count"
          className="absolute top-1 right-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-red-600 px-1 text-[11px] leading-none font-bold text-white ring-2 ring-white"
        >
          {badgeText(count)}
        </span>
      )}
    </NavLink>
  )
}
