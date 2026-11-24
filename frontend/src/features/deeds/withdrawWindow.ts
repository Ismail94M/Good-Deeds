/**
 * The 24-hour withdrawal window (M4-DESIGN D64, §6.3). The server decides: `can_withdraw` is
 * false once the window has passed, and `withdrawable_until` (verified deeds only) is when it
 * ends. The client only hides the button when that moment passes while the page is open; the
 * withdraw call itself is re-checked by the server (409 `withdraw_window_closed`).
 */

import { useEffect, useState } from 'react'
import { LAUNCH_TIME_ZONE } from '../report/format'
import type { DeedOut } from './deedsApi'

const untilFormat = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: LAUNCH_TIME_ZONE,
})

/** "3 Dec, 14:05" in IST (the launch city's time, like the other deed dates). */
export function formatWithdrawUntil(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const parts = untilFormat.formatToParts(date)
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? ''
  return `${get('day')} ${get('month')}, ${get('hour')}:${get('minute')}`
}

// setTimeout's largest delay; longer windows simply re-check after this.
const MAX_DELAY_MS = 2_147_483_647

/**
 * Whether the Withdraw button shows now: `can_withdraw` and, for a verified deed, not yet past
 * `withdrawable_until`. Re-renders when the window ends while the page is open.
 */
export function useCanWithdraw(deed: DeedOut): boolean {
  const until = deed.withdrawable_until ? Date.parse(deed.withdrawable_until) : null
  const [now, setNow] = useState(() => Date.now())
  const open = until === null || Number.isNaN(until) || now < until

  useEffect(() => {
    if (!deed.can_withdraw || until === null || Number.isNaN(until)) return
    const left = until - Date.now()
    if (left <= 0) return
    const timer = setTimeout(() => setNow(Date.now()), Math.min(left + 50, MAX_DELAY_MS))
    return () => clearTimeout(timer)
  }, [deed.can_withdraw, until, now])

  return deed.can_withdraw && open
}

/** "Verified deeds can be withdrawn only in the first 24 hours." with the hours from config. */
export function windowClosedNote(hours: number | undefined): string {
  return hours
    ? `Verified deeds can be withdrawn only in the first ${hours} hours.`
    : 'Verified deeds can be withdrawn only for a short time after they are verified.'
}
