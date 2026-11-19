import { useCallback, useEffect, useRef } from 'react'
import type { PhotoOut } from '../media/mediaApi'

/** Refetch this long before the earliest signed URL expires (§4.7, §6.4). */
export const REFRESH_MARGIN_MS = 5 * 60_000
/** At most one error-triggered refetch in this window, so a broken image can't loop. */
const ERROR_REFETCH_GAP_MS = 30_000

/** The earliest `urls_expire_at` among these photos, in ms, or null. */
export function earliestExpiry(photos: Iterable<PhotoOut>): number | null {
  let earliest: number | null = null
  for (const photo of photos) {
    const at = Date.parse(photo.urls_expire_at)
    if (!Number.isNaN(at) && (earliest === null || at < earliest)) earliest = at
  }
  return earliest
}

/**
 * Keeps signed photo URLs fresh: refetches when the earliest `urls_expire_at` is less than five
 * minutes away, and when an image fails to load (throttled). Returns the `onImageError` handler.
 */
export function useSignedUrlRefresh(photos: PhotoOut[], refetch: () => unknown): () => void {
  const refetchRef = useRef(refetch)
  useEffect(() => {
    refetchRef.current = refetch
  }, [refetch])
  const lastErrorRefetch = useRef(0)

  const expiry = earliestExpiry(photos)
  useEffect(() => {
    if (expiry === null) return
    const delay = expiry - REFRESH_MARGIN_MS - Date.now()
    // Already inside the margin: refresh once now (the server signs at least 30 min ahead).
    const timer = window.setTimeout(() => void refetchRef.current(), Math.max(0, delay))
    return () => window.clearTimeout(timer)
  }, [expiry])

  return useCallback(() => {
    const now = Date.now()
    if (now - lastErrorRefetch.current < ERROR_REFETCH_GAP_MS) return
    lastErrorRefetch.current = now
    void refetchRef.current()
  }, [])
}
