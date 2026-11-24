import { createContext, useContext } from 'react'
import type { Location } from '../../lib/geolocation'

/** A position the witness's browser gave us, with when (ms) we got it. */
export type HeldLocation = { location: Location; at: number }

/** A held position older than this is refreshed before `POST /api/scans` (§5.3). */
export const LOCATION_MAX_AGE_MS = 60_000

/**
 * State shared by the Record a deed routes while the witness moves between them (hub, camera,
 * ID + code, `/scan/:token`): the position, so the location gate isn't shown twice. In memory
 * only; it is gone after a reload or on leaving the section.
 */
export type ReportContextValue = {
  held: HeldLocation | null
  setHeld: (held: HeldLocation | null) => void
  /**
   * A QR token from a `/scan/:token` link, held in memory only so the URL can be replaced
   * straight away (security review R5-L2: the token must not stay in history or logs).
   */
  pendingToken: string | null
  setPendingToken: (token: string | null) => void
}

export const ReportContext = createContext<ReportContextValue | null>(null)

export function useReportContext(): ReportContextValue {
  const value = useContext(ReportContext)
  if (!value) throw new Error('useReportContext must be used inside <ReportLayout>')
  return value
}

/**
 * Thrown by `LocationGateApi.freshLocation()` when no usable position could be had. The gate
 * has already switched to its denied / imprecise / unavailable screen, so callers just stop.
 */
export class LocationNotReady extends Error {
  constructor() {
    super('Location is not ready.')
    this.name = 'LocationNotReady'
  }
}

export type LocationGateApi = {
  /** The held position (shown in the "Location ready" chip). */
  location: Location
  /** The held position if it is under a minute old, else a new one (may throw LocationNotReady). */
  freshLocation: () => Promise<Location>
  /** Show the "not precise enough" screen, e.g. after a 400 `location_too_imprecise`. */
  showImprecise: (accuracyM: number) => void
}
