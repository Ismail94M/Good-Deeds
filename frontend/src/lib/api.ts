/** Fetch wrapper for the Good Deeds API (served under /api). All HTTP goes through here. */

import { getDeviceId } from './deviceId'

const FALLBACK_MESSAGE = 'Something went wrong. Please try again.'

export class ApiError extends Error {
  readonly status: number
  /** User-safe message from the API (or a friendly fallback). */
  readonly detail: string
  /** Machine-readable error code, when the API sent one. */
  readonly code: string | null
  /** Per-field messages from a 422 validation error, keyed by field name. */
  readonly fieldErrors: Record<string, string>

  constructor(
    status: number,
    detail: string,
    code: string | null = null,
    fieldErrors: Record<string, string> = {},
  ) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
    this.fieldErrors = fieldErrors
  }
}

/** Friendly message for any thrown value (ApiError detail, or a generic fallback). */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail
  return 'We could not reach Good Deeds. Check your connection and try again.'
}

export type TokenResponse = {
  access_token: string
  token_type: 'bearer'
  expires_at: string
}

// ----------------------------------------------------------------- access token (memory only)

let accessToken: string | null = null
let sessionExpiredListener: (() => void) | null = null

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null): void {
  accessToken = token
}

/** Called when a request is rejected and the session could not be refreshed. */
export function onSessionExpired(listener: (() => void) | null): void {
  sessionExpiredListener = listener
}

function baseHeaders(): Record<string, string> {
  return { Accept: 'application/json', 'X-Device-Id': getDeviceId() }
}

/** Result of a refresh attempt: a token, or the error that stopped it (null when offline). */
type RefreshOutcome = { token: string | null; error: ApiError | null }

async function refreshRequest(): Promise<RefreshOutcome> {
  try {
    const response = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: baseHeaders(),
      credentials: 'include',
    })
    if (!response.ok) {
      accessToken = null
      return { token: null, error: await toApiError(response) }
    }
    const body = (await response.json()) as TokenResponse
    accessToken = body.access_token
    return { token: accessToken, error: null }
  } catch {
    return { token: null, error: null }
  }
}

type LockManagerLike = {
  request: <T>(name: string, callback: () => Promise<T>) => Promise<T>
}

/**
 * Refresh tokens rotate on every use, so two tabs refreshing at once could look like token
 * reuse. Where the Web Locks API exists, refreshes are serialised across tabs.
 */
function doRefresh(): Promise<RefreshOutcome> {
  const locks =
    typeof navigator !== 'undefined'
      ? (navigator as Navigator & { locks?: LockManagerLike }).locks
      : undefined
  if (locks && typeof locks.request === 'function') {
    return locks.request('gd-refresh', refreshRequest).catch(() => refreshRequest())
  }
  return refreshRequest()
}

let refreshOutcome: Promise<RefreshOutcome> | null = null

function sharedRefresh(): Promise<RefreshOutcome> {
  if (!refreshOutcome) {
    const pending = doRefresh()
    refreshOutcome = pending
    void pending.finally(() => {
      if (refreshOutcome === pending) refreshOutcome = null
    })
  }
  return refreshOutcome
}

/**
 * Exchange the httpOnly refresh cookie for a new access token. Parallel callers share
 * one request, so the refresh token is only rotated once. Resolves to null when logged out
 * (or when refreshing is rate limited / offline).
 */
export async function refreshAccessToken(): Promise<string | null> {
  return (await sharedRefresh()).token
}

// ----------------------------------------------------------------- errors

type ValidationItem = { loc?: unknown[]; msg?: string }

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // not JSON
  }
  if (body && typeof body === 'object' && 'detail' in body) {
    const { detail, code } = body as { detail: unknown; code?: unknown }
    const codeValue = typeof code === 'string' ? code : null
    if (typeof detail === 'string') {
      return new ApiError(response.status, detail, codeValue)
    }
    if (Array.isArray(detail)) {
      const fieldErrors: Record<string, string> = {}
      for (const item of detail as ValidationItem[]) {
        const field = item.loc?.[item.loc.length - 1]
        if (typeof field === 'string' && item.msg && !fieldErrors[field]) {
          fieldErrors[field] = item.msg.replace(/^Value error, /, '')
        }
      }
      return new ApiError(
        response.status,
        'Some details need fixing. Please check the form and try again.',
        codeValue ?? 'validation_error',
        fieldErrors,
      )
    }
  }
  if (response.status === 429) {
    return new ApiError(429, 'Too many attempts. Please wait a moment and try again.')
  }
  return new ApiError(response.status, FALLBACK_MESSAGE)
}

// ----------------------------------------------------------------- requests

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  allowRetry = true,
): Promise<T> {
  const headers = baseHeaders()
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (response.status === 401 && allowRetry && !path.startsWith('/auth/')) {
    const outcome = await sharedRefresh()
    if (outcome.token) return request<T>(method, path, body, false)
    // Rate limited while refreshing: surface that, but don't treat it as a logout.
    if (outcome.error?.status === 429) throw outcome.error
    if (outcome.error) sessionExpiredListener?.()
  }
  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const apiGet = <T>(path: string) => request<T>('GET', path)
export const apiPost = <T>(path: string, body?: unknown) => request<T>('POST', path, body)
export const apiPatch = <T>(path: string, body?: unknown) => request<T>('PATCH', path, body)

/** Test helper: clear in-memory auth state. */
export function resetApiForTests(): void {
  accessToken = null
  refreshOutcome = null
  sessionExpiredListener = null
}

// ----------------------------------------------------------------- health

export type Health = {
  status: 'ok' | 'degraded'
  database: 'ok' | 'unavailable'
  postgis: string | null
}

export const getHealth = () => apiGet<Health>('/health')
