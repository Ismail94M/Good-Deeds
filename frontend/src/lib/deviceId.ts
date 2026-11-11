/** A random per-browser ID sent as X-Device-Id (used by the API for fraud prevention). */

const STORAGE_KEY = 'gd_device_id'
let cached: string | null = null

function newId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
  } catch {
    // fall through to the manual version
  }
  const hex = '0123456789abcdef'
  let out = ''
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) out += '-'
    else if (i === 14) out += '4'
    else out += hex[Math.floor(Math.random() * 16)]
  }
  return out
}

export function getDeviceId(): string {
  if (cached) return cached
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored) {
      cached = stored
      return stored
    }
    const id = newId()
    cached = id
    window.localStorage.setItem(STORAGE_KEY, id)
    return id
  } catch {
    // Storage blocked (private mode, disabled cookies): keep an in-memory ID for this tab.
    cached ??= newId()
    return cached
  }
}

/** Test helper: forget the cached ID. */
export function resetDeviceIdForTests(): void {
  cached = null
}
