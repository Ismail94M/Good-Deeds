import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  GeolocationError,
  getPosition,
  permissionState,
  POSITION_OPTIONS,
  toAccuracyM,
} from './geolocation'

const ERROR_CODES = { PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 }

function positionError(code: number): GeolocationPositionError {
  return { code, message: 'x', ...ERROR_CODES } as GeolocationPositionError
}

function position(latitude: number, longitude: number, accuracy: number): GeolocationPosition {
  return { coords: { latitude, longitude, accuracy }, timestamp: 0 } as GeolocationPosition
}

type Outcome = { position: GeolocationPosition } | { error: GeolocationPositionError }

function stubGeolocation(outcome: Outcome) {
  const getCurrentPosition = vi.fn(
    (
      success: PositionCallback,
      failure?: PositionErrorCallback | null,
      _options?: PositionOptions,
    ) => {
      if ('position' in outcome) success(outcome.position)
      else failure?.(outcome.error)
    },
  )
  Object.defineProperty(navigator, 'geolocation', {
    value: { getCurrentPosition },
    configurable: true,
  })
  return getCurrentPosition
}

afterEach(() => {
  Object.defineProperty(navigator, 'geolocation', { value: undefined, configurable: true })
  Object.defineProperty(navigator, 'permissions', { value: undefined, configurable: true })
})

function stubPermissions(query: (descriptor: PermissionDescriptor) => Promise<unknown>) {
  const spy = vi.fn(query)
  Object.defineProperty(navigator, 'permissions', { value: { query: spy }, configurable: true })
  return spy
}

describe('getPosition', () => {
  it('resolves to the API Location shape with high-accuracy, fresh, 15 s options', async () => {
    const spy = stubGeolocation({ position: position(12.9716, 77.5946, 18.4) })

    await expect(getPosition()).resolves.toEqual({ lat: 12.9716, lng: 77.5946, accuracy_m: 19 })
    expect(spy.mock.calls[0][2]).toEqual({
      enableHighAccuracy: true,
      maximumAge: 0,
      timeout: 15000,
    })
    expect(POSITION_OPTIONS).toEqual(spy.mock.calls[0][2])
  })

  it.each([
    [ERROR_CODES.PERMISSION_DENIED, 'denied'],
    [ERROR_CODES.POSITION_UNAVAILABLE, 'unavailable'],
    [ERROR_CODES.TIMEOUT, 'timeout'],
  ] as const)('maps browser error code %i to "%s"', async (code, kind) => {
    stubGeolocation({ error: positionError(code) })

    const error = await getPosition().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(GeolocationError)
    expect((error as GeolocationError).kind).toBe(kind)
  })

  it('rejects as unavailable when the browser has no geolocation', async () => {
    const error = await getPosition().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(GeolocationError)
    expect((error as GeolocationError).kind).toBe('unavailable')
  })
})

describe('toAccuracyM', () => {
  it('rounds up to whole metres within 1..100000', () => {
    expect(toAccuracyM(0)).toBe(1)
    expect(toAccuracyM(0.2)).toBe(1)
    expect(toAccuracyM(12)).toBe(12)
    expect(toAccuracyM(12.01)).toBe(13)
    expect(toAccuracyM(250000)).toBe(100000)
    expect(toAccuracyM(Number.NaN)).toBe(100000)
    expect(toAccuracyM(Number.POSITIVE_INFINITY)).toBe(100000)
  })
})

describe('permissionState', () => {
  it.each(['granted', 'prompt', 'denied'] as const)(
    'returns "%s" from the Permissions API',
    async (state) => {
      const spy = stubPermissions(() => Promise.resolve({ state }))
      await expect(permissionState()).resolves.toBe(state)
      expect(spy).toHaveBeenCalledWith({ name: 'geolocation' })
    },
  )

  it('returns unknown without the Permissions API', async () => {
    await expect(permissionState()).resolves.toBe('unknown')
  })

  it('returns unknown when the query fails or reports something else', async () => {
    stubPermissions(() => Promise.reject(new TypeError('not supported')))
    await expect(permissionState()).resolves.toBe('unknown')
    stubPermissions(() => Promise.resolve({ state: 'weird' }))
    await expect(permissionState()).resolves.toBe('unknown')
  })
})
