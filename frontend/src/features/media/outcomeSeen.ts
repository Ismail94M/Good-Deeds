/**
 * "Unread" review outcomes for the dot on My deeds cards (docs/M3-DESIGN.md §6.3, _interim_):
 * per device, in local storage. Every access is wrapped, so a private window or blocked storage
 * just means no dot, never an error.
 */

import { useQuery } from '@tanstack/react-query'
import type { DeedOut } from '../deeds/deedsApi'
import { mediaApi, mediaKeys, type Outcome } from './mediaApi'

const PREFIX = 'gd.photoOutcomesSeen.'

export function latestOutcomeAt(outcomes: Outcome[]): string | null {
  let latest: string | null = null
  for (const outcome of outcomes) {
    if (latest === null || Date.parse(outcome.decided_at) > Date.parse(latest)) {
      latest = outcome.decided_at
    }
  }
  return latest
}

function readSeen(deedId: string): string | null {
  try {
    return window.localStorage.getItem(PREFIX + deedId)
  } catch {
    return null
  }
}

/** Remember the newest outcome shown on the deed's detail page. */
export function markOutcomesSeen(deedId: string, outcomes: Outcome[]): void {
  const latest = latestOutcomeAt(outcomes)
  if (!latest) return
  try {
    window.localStorage.setItem(PREFIX + deedId, latest)
  } catch {
    // Storage blocked: the dot just keeps showing on this device.
  }
}

export function hasUnreadOutcome(deedId: string, outcomes: Outcome[]): boolean {
  const latest = latestOutcomeAt(outcomes)
  if (!latest) return false
  const seen = readSeen(deedId)
  return seen === null || Date.parse(latest) > Date.parse(seen)
}

/**
 * Whether the reporter has an outcome they haven't opened. Reads the same cached
 * `GET /media/deeds/{id}/photos` as the detail page; only verified deeds can have reports.
 */
export function useUnreadOutcome(deed: DeedOut, isReporter: boolean): boolean {
  const query = useQuery({
    queryKey: mediaKeys.deed(deed.id),
    queryFn: () => mediaApi.deedPhotos(deed.id),
    enabled: isReporter && deed.status === 'verified',
    retry: false,
    staleTime: 5 * 60_000,
  })
  return query.data ? hasUnreadOutcome(deed.id, query.data.outcomes) : false
}
