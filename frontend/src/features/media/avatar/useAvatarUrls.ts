import { useQueries, useQuery, type Query } from '@tanstack/react-query'
import { useContext } from 'react'
import { AuthContext } from '../../auth/authContext'
import { avatarApi, avatarKeys, type AvatarUrls } from './avatarApi'

/** `avatar_sign_max_refs` (D117): the server refuses bigger batches. */
export const MAX_REFS_PER_CALL = 100
/** Refs asked for within this window go into one `POST /media/avatars/urls` (§6.1). */
export const BATCH_WINDOW_MS = 10
/** Refetch this long before the signed URLs expire (§6.1: `expires_at` minus 5 min). */
export const AVATAR_REFRESH_MARGIN_MS = 5 * 60_000
const MAX_TIMER_MS = 2 ** 31 - 1
/** Keep resolved URLs in memory for a day: the URL lifetime (D118). */
const GC_MS = 24 * 60 * 60_000

type Waiter = { resolve: (urls: AvatarUrls | null) => void; reject: (error: unknown) => void }

let queue = new Map<string, Waiter[]>()
let timer: ReturnType<typeof setTimeout> | null = null

async function flush() {
  timer = null
  const batch = queue
  queue = new Map()
  const refs = [...batch.keys()]
  for (let start = 0; start < refs.length; start += MAX_REFS_PER_CALL) {
    const chunk = refs.slice(start, start + MAX_REFS_PER_CALL)
    try {
      const { items } = await avatarApi.urls(chunk)
      for (const ref of chunk) {
        for (const waiter of batch.get(ref) ?? []) waiter.resolve(items?.[ref] ?? null)
      }
    } catch (error) {
      for (const ref of chunk) for (const waiter of batch.get(ref) ?? []) waiter.reject(error)
    }
  }
}

/**
 * Ask for one ref's URLs. Every ref asked for in the same short window, from any component,
 * is sent in one deduplicated call (chunks of 100).
 */
export function loadAvatarUrls(ref: string): Promise<AvatarUrls | null> {
  return new Promise((resolve, reject) => {
    const waiters = queue.get(ref)
    if (waiters) waiters.push({ resolve, reject })
    else queue.set(ref, [{ resolve, reject }])
    if (timer === null) timer = setTimeout(() => void flush(), BATCH_WINDOW_MS)
  })
}

/** Test helper: forget queued refs. */
export function resetAvatarBatchForTests() {
  if (timer !== null) clearTimeout(timer)
  timer = null
  queue = new Map()
}

type AvatarQuery = Query<AvatarUrls | null, Error, AvatarUrls | null, readonly unknown[]>

/** Milliseconds until these URLs should be swapped, or null when there is nothing to swap. */
function msUntilRefresh(data: AvatarUrls | null | undefined): number | null {
  if (!data) return null
  const at = Date.parse(data.expires_at)
  if (Number.isNaN(at)) return null
  return Math.max(0, at - AVATAR_REFRESH_MARGIN_MS - Date.now())
}

function avatarQueryOptions(ref: string, enabled: boolean) {
  return {
    queryKey: avatarKeys.url(ref),
    queryFn: () => loadAvatarUrls(ref),
    enabled,
    retry: false,
    gcTime: GC_MS,
    refetchOnWindowFocus: false,
    // Fresh until 5 minutes before the URLs expire; a ref that isn't live stays null for a while.
    staleTime: (query: AvatarQuery) => msUntilRefresh(query.state.data) ?? 10 * 60_000,
    refetchInterval: (query: AvatarQuery) => {
      const ms = msUntilRefresh(query.state.data)
      return ms === null ? false : Math.min(MAX_TIMER_MS, Math.max(1_000, ms))
    },
  }
}

/** Pictures are shown to signed-in members only (D119); visitors always get the default. */
function useSignedIn(): boolean {
  return Boolean(useContext(AuthContext)?.user)
}

/**
 * Signed URLs for one ref (null ref, signed out, not live, still loading or failed: no URLs).
 * Also returns `refetch`, used once when an image fails to load (§6.2).
 */
export function useAvatarUrl(ref: string | null | undefined) {
  const signedIn = useSignedIn()
  const query = useQuery(avatarQueryOptions(ref ?? '', Boolean(ref) && signedIn))
  return { urls: ref && signedIn ? (query.data ?? null) : null, refetch: query.refetch }
}

/**
 * Signed URLs for many refs at once, e.g. a whole list. The loader batches them into one call,
 * and each ref is cached on its own, so another screen showing the same face asks for nothing.
 */
export function useAvatarUrls(
  refs: (string | null | undefined)[],
): Record<string, AvatarUrls | null> {
  const signedIn = useSignedIn()
  const unique = [...new Set(refs.filter((ref): ref is string => Boolean(ref)))]
  const results = useQueries({
    queries: unique.map((ref) => avatarQueryOptions(ref, signedIn)),
  })
  const out: Record<string, AvatarUrls | null> = {}
  unique.forEach((ref, index) => {
    out[ref] = signedIn ? (results[index]?.data ?? null) : null
  })
  return out
}
