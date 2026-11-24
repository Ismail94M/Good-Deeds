/**
 * A witness who opened a `/scan/:token` link while signed out (or before finishing setup) is
 * sent to log in with `next=/report`, never with the token (R5-L2). By the time they are back
 * the code has expired (§5.3 Q6), so the hub tells them to ask for a new one. The flag lives in
 * sessionStorage for this tab only and holds no token.
 */

const KEY = 'gd-rescan-notice'

export function setRescanNotice(): void {
  try {
    window.sessionStorage.setItem(KEY, '1')
  } catch {
    // Storage blocked: the hub simply shows no notice.
  }
}

/** Whether the notice is due (no side effect, so safe in a state initializer). */
export function hasRescanNotice(): boolean {
  try {
    return window.sessionStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

/** Show it once: the hub clears it after reading. */
export function clearRescanNotice(): void {
  try {
    window.sessionStorage.removeItem(KEY)
  } catch {
    // ignore
  }
}
