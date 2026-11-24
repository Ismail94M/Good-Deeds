/**
 * The QR token from a scanned value (M2-DESIGN §5.1). Only a URL on this site's own origin
 * whose path is `/scan/:token` counts; anything else (another site's QR, plain text) is null.
 */
export function parseScanUrl(raw: string, origin: string = window.location.origin): string | null {
  let url: URL
  try {
    url = new URL(raw.trim())
  } catch {
    return null
  }
  if (url.origin !== origin) return null
  const match = /^\/scan\/([^/]+)\/?$/.exec(url.pathname)
  if (!match) return null
  try {
    const token = decodeURIComponent(match[1])
    return token || null
  } catch {
    return null
  }
}
