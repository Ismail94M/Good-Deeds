/** Thin fetch wrapper for the Good Deeds API (served under /api). */

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { Accept: 'application/json' },
    credentials: 'include',
  })
  if (!response.ok) {
    throw new ApiError(response.status, `GET ${path} failed with ${response.status}`)
  }
  return (await response.json()) as T
}

export type Health = {
  status: 'ok' | 'degraded'
  database: 'ok' | 'unavailable'
  postgis: string | null
}

export const getHealth = () => apiGet<Health>('/health')
