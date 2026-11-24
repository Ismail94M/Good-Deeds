/** One-shot, high-accuracy browser geolocation, shaped as the API's `Location` (M2-DESIGN §3.1). */

/** Shared request type for every M2 endpoint that takes a location. */
export type Location = {
  /** -90..90 */
  lat: number
  /** -180..180 */
  lng: number
  /** Whole metres, 1..100000. */
  accuracy_m: number
}

export type GeolocationErrorKind = 'denied' | 'unavailable' | 'timeout'

export class GeolocationError extends Error {
  readonly kind: GeolocationErrorKind

  constructor(kind: GeolocationErrorKind, message: string) {
    super(message)
    this.name = 'GeolocationError'
    this.kind = kind
  }
}

export const POSITION_OPTIONS: PositionOptions = {
  enableHighAccuracy: true,
  maximumAge: 0,
  timeout: 15000,
}

const MIN_ACCURACY_M = 1
const MAX_ACCURACY_M = 100000

/**
 * The API takes an integer accuracy in 1..100000. Round up (never claim better accuracy
 * than the browser reported) and clamp, so a very poor fix still reaches the server and gets
 * its friendly `location_too_imprecise` error instead of a validation error.
 */
export function toAccuracyM(accuracy: number): number {
  if (!Number.isFinite(accuracy)) return MAX_ACCURACY_M
  return Math.min(MAX_ACCURACY_M, Math.max(MIN_ACCURACY_M, Math.ceil(accuracy)))
}

function toGeolocationError(error: GeolocationPositionError): GeolocationError {
  switch (error.code) {
    case error.PERMISSION_DENIED:
      return new GeolocationError('denied', 'Location permission was denied.')
    case error.TIMEOUT:
      return new GeolocationError('timeout', 'Finding your location took too long.')
    default:
      return new GeolocationError('unavailable', 'Your location is not available right now.')
  }
}

/** Ask the browser for a fresh, high-accuracy position. Rejects with a `GeolocationError`. */
export function getPosition(): Promise<Location> {
  return new Promise((resolve, reject) => {
    const geolocation = typeof navigator !== 'undefined' ? navigator.geolocation : undefined
    if (!geolocation || typeof geolocation.getCurrentPosition !== 'function') {
      reject(new GeolocationError('unavailable', 'This browser cannot share your location.'))
      return
    }
    geolocation.getCurrentPosition(
      (position) =>
        resolve({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          accuracy_m: toAccuracyM(position.coords.accuracy),
        }),
      (error) => reject(toGeolocationError(error)),
      POSITION_OPTIONS,
    )
  })
}

export type LocationPermission = 'granted' | 'prompt' | 'denied' | 'unknown'

/**
 * The browser's current geolocation permission, without prompting. `'unknown'` when the
 * Permissions API is missing (older Safari) or the query fails; treat it like `'prompt'`.
 */
export async function permissionState(): Promise<LocationPermission> {
  const permissions = typeof navigator !== 'undefined' ? navigator.permissions : undefined
  if (!permissions || typeof permissions.query !== 'function') return 'unknown'
  try {
    const status = await permissions.query({ name: 'geolocation' })
    const state: string = status.state
    return state === 'granted' || state === 'prompt' || state === 'denied' ? state : 'unknown'
  } catch {
    return 'unknown'
  }
}
