/** Client-side helpers for notification rows: where a row leads and how old it is (§6.3). */

import type { NotificationLink } from './notificationsApi'

const seg = encodeURIComponent

/** The route for a row's link (N1 `link`), or null when the row leads nowhere. */
export function linkHref(link: NotificationLink | null): string | null {
  if (!link) return null
  const target = link.target
  switch (link.kind) {
    case 'deed':
      return target ? `/deeds/${seg(target)}` : null
    case 'my_deed':
      return target ? `/me/deeds/${seg(target)}` : '/me/deeds'
    case 'profile':
      return target ? `/u/${seg(target)}` : '/profile'
    case 'reviews':
      return '/me/reviews'
    case 'home':
      return '/'
    case 'badges':
      return '/profile'
    case 'guidelines':
      return target === 'community' ? '/community-guidelines' : '/photo-guidelines'
    case 'connections':
      return `/me/connections?tab=${target === 'following' ? 'following' : 'followers'}`
    case 'settings':
      return '/settings/notifications'
    default:
      return null
  }
}

const shortDate = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  timeZone: 'Asia/Kolkata',
})

/** "Just now", "5m", "3h", "2d", "1w", then "14 Nov" (social-app style). */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return ''
  const minutes = Math.floor((now - at) / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d`
  if (days < 28) return `${Math.floor(days / 7)}w`
  return shortDate.format(new Date(at))
}

/** Spoken form of `relativeTime`: "5 minutes ago". */
export function relativeTimeLong(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return ''
  const minutes = Math.floor((now - at) / 60_000)
  const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? '' : 's'} ago`
  if (minutes < 1) return 'just now'
  if (minutes < 60) return plural(minutes, 'minute')
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return plural(hours, 'hour')
  const days = Math.floor(hours / 24)
  if (days < 28) return plural(days, 'day')
  return `on ${shortDate.format(new Date(at))}`
}
