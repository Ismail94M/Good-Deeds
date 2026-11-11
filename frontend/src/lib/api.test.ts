import { describe, expect, it, vi } from 'vitest'
import { mockApi, TOKEN } from '../test/utils'
import {
  ApiError,
  apiGet,
  apiPatch,
  apiPost,
  getAccessToken,
  onSessionExpired,
  refreshAccessToken,
  setAccessToken,
} from './api'

describe('api client', () => {
  it('sends credentials, the device ID and the bearer token', async () => {
    const api = mockApi({ 'GET /users/me': { body: { ok: true } } })
    setAccessToken('abc')
    await apiGet('/users/me')

    const [url, init] = api.spy.mock.calls[0]
    expect(url).toBe('/api/users/me')
    expect(init?.credentials).toBe('include')
    const req = api.requests[0]
    expect(req.headers.get('Authorization')).toBe('Bearer abc')
    const deviceId = req.headers.get('X-Device-Id')
    expect(deviceId).toMatch(/^[0-9a-f-]{36}$/)
    expect(window.localStorage.getItem('gd_device_id')).toBe(deviceId)

    await apiGet('/users/me')
    expect(api.requests[1].headers.get('X-Device-Id')).toBe(deviceId)
  })

  it('keeps working with an in-memory device ID when storage is blocked', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const api = mockApi({ 'GET /health': { body: {} } })
    await apiGet('/health')
    await apiGet('/health')
    const first = api.requests[0].headers.get('X-Device-Id')
    expect(first).toBeTruthy()
    expect(api.requests[1].headers.get('X-Device-Id')).toBe(first)
  })

  it('refreshes once on 401 and retries with the new token', async () => {
    const api = mockApi({
      'POST /auth/refresh': { body: { ...TOKEN, access_token: 'fresh' } },
      'GET /users/me': (req) =>
        req.headers.get('Authorization') === 'Bearer fresh'
          ? { body: { handle: 'asha' } }
          : { status: 401, body: { detail: 'Please log in again.', code: 'invalid_token' } },
    })
    setAccessToken('expired')

    await expect(apiGet('/users/me')).resolves.toEqual({ handle: 'asha' })
    expect(api.calls('POST /auth/refresh')).toHaveLength(1)
    expect(api.calls('GET /users/me')).toHaveLength(2)
    expect(getAccessToken()).toBe('fresh')
  })

  it('shares a single refresh between parallel requests', async () => {
    let releaseRefresh: () => void = () => {}
    const refreshGate = new Promise<void>((resolve) => (releaseRefresh = resolve))
    const api = mockApi({
      'POST /auth/refresh': async () => {
        await refreshGate
        return { body: { ...TOKEN, access_token: 'fresh' } }
      },
      'GET /users/me': (req) =>
        req.headers.get('Authorization') === 'Bearer fresh'
          ? { body: { n: 1 } }
          : { status: 401, body: { detail: 'Please log in again.' } },
      'GET /users/me/qr': (req) =>
        req.headers.get('Authorization') === 'Bearer fresh'
          ? { body: { n: 2 } }
          : { status: 401, body: { detail: 'Please log in again.' } },
    })
    setAccessToken('expired')

    const both = Promise.all([apiGet('/users/me'), apiGet('/users/me/qr'), apiGet('/users/me')])
    await vi.waitFor(() => expect(api.calls('POST /auth/refresh')).toHaveLength(1))
    releaseRefresh()

    await expect(both).resolves.toEqual([{ n: 1 }, { n: 2 }, { n: 1 }])
    expect(api.calls('POST /auth/refresh')).toHaveLength(1)
  })

  it('gives up and reports the session as expired when refresh fails', async () => {
    const api = mockApi({
      'GET /users/me': {
        status: 401,
        body: { detail: 'Please log in.', code: 'not_authenticated' },
      },
    })
    const expired = vi.fn()
    onSessionExpired(expired)
    setAccessToken('expired')

    const error = await apiGet('/users/me').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 401, code: 'not_authenticated' })
    expect(expired).toHaveBeenCalledOnce()
    expect(api.calls('GET /users/me')).toHaveLength(1)
    expect(getAccessToken()).toBeNull()
  })

  it('surfaces a rate-limited refresh without logging the user out', async () => {
    mockApi({
      'POST /auth/refresh': {
        status: 429,
        body: { detail: 'Too many requests. Please wait a minute.', code: 'rate_limited' },
      },
      'GET /users/me': { status: 401, body: { detail: 'Please log in again.' } },
    })
    const expired = vi.fn()
    onSessionExpired(expired)
    setAccessToken('expired')

    await expect(apiGet('/users/me')).rejects.toMatchObject({ status: 429, code: 'rate_limited' })
    expect(expired).not.toHaveBeenCalled()
  })

  it('treats a rate-limited refresh on app load as logged out', async () => {
    mockApi({ 'POST /auth/refresh': { status: 429, body: { detail: 'Slow down.' } } })
    await expect(refreshAccessToken()).resolves.toBeNull()
  })

  it('serialises refreshes across tabs with the Web Locks API when available', async () => {
    const request = vi.fn((_name: string, callback: () => Promise<unknown>) => callback())
    Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true })
    try {
      mockApi({ 'POST /auth/refresh': { body: TOKEN } })
      await expect(refreshAccessToken()).resolves.toBe('access-1')
      expect(request).toHaveBeenCalledWith('gd-refresh', expect.any(Function))
    } finally {
      delete (navigator as { locks?: unknown }).locks
    }
  })

  it('never refreshes for /auth endpoints', async () => {
    const api = mockApi({
      'POST /auth/login': {
        status: 401,
        body: { detail: 'Incorrect email or password.', code: 'bad_credentials' },
      },
    })
    const error = await apiPost('/auth/login', {}).catch((e: unknown) => e)
    expect(error).toMatchObject({ detail: 'Incorrect email or password.', code: 'bad_credentials' })
    expect(api.calls('POST /auth/refresh')).toHaveLength(0)
  })

  it('turns 422 validation errors into field messages', async () => {
    mockApi({
      'PATCH /users/me': {
        status: 422,
        body: {
          detail: [{ loc: ['body', 'handle'], msg: 'Value error, This handle is reserved' }],
        },
      },
    })
    const error = (await apiPatch('/users/me', { handle: 'admin' }).catch(
      (e: unknown) => e,
    )) as ApiError
    expect(error.status).toBe(422)
    expect(error.fieldErrors).toEqual({ handle: 'This handle is reserved' })
    expect(error.detail).toMatch(/check the form/i)
  })

  it('handles 204 and non-JSON errors', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response('Bad gateway', { status: 502 }))
    await expect(apiPost('/auth/logout')).resolves.toBeUndefined()
    await expect(apiGet('/health')).rejects.toMatchObject({
      status: 502,
      detail: 'Something went wrong. Please try again.',
    })
  })
})
