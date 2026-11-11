/** Shared test helpers: a routing fetch mock and a full-app renderer. */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { vi } from 'vitest'
import { routes } from '../app/routes'
import { AuthProvider } from '../features/auth/AuthProvider'
import type { Me, QrCode } from '../features/auth/identityApi'

export type Reply = { status?: number; body?: unknown }
export type MockRequest = { method: string; path: string; body: unknown; headers: Headers }
type Handler = Reply | Reply[] | ((req: MockRequest) => Reply | Promise<Reply>)

export function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const DEFAULTS: Record<string, Handler> = {
  'POST /auth/refresh': {
    status: 401,
    body: { detail: 'Please log in.', code: 'not_authenticated' },
  },
  'GET /auth/config': { body: { google_client_id: null, facebook_app_id: null } },
  'GET /health': { body: { status: 'ok', database: 'ok', postgis: '3.4' } },
}

/**
 * Mock fetch with handlers keyed by "METHOD /path" (path without the /api prefix).
 * An array replies with its items in turn (the last one repeats).
 */
export function mockApi(handlers: Record<string, Handler> = {}) {
  const all: Record<string, Handler> = { ...DEFAULTS, ...handlers }
  const counters = new Map<string, number>()
  const requests: MockRequest[] = []

  const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const path = url.replace(/^\/api/, '').split('?')[0]
    const method = (init?.method ?? 'GET').toUpperCase()
    const rawBody = init?.body
    const req: MockRequest = {
      method,
      path,
      body: typeof rawBody === 'string' ? JSON.parse(rawBody) : undefined,
      headers: new Headers(init?.headers),
    }
    requests.push(req)
    const key = `${method} ${path}`
    const handler = all[key]
    if (!handler) return json(404, { detail: `No mock for ${key}`, code: 'not_found' })
    let reply: Reply
    if (typeof handler === 'function') reply = await handler(req)
    else if (Array.isArray(handler)) {
      const n = counters.get(key) ?? 0
      counters.set(key, n + 1)
      reply = handler[Math.min(n, handler.length - 1)]
    } else reply = handler
    return json(reply.status ?? 200, reply.body)
  })

  return {
    spy,
    requests,
    calls: (key: string) => requests.filter((r) => `${r.method} ${r.path}` === key),
  }
}

export const TOKEN = {
  access_token: 'access-1',
  token_type: 'bearer',
  expires_at: '2026-11-11T12:00:00Z',
}

export function makeMe(overrides: Partial<Me> = {}): Me {
  const complete = overrides.onboarding?.complete ?? true
  return {
    id: '00000000-0000-0000-0000-000000000001',
    email: 'asha@example.com',
    display_name: 'Asha',
    handle: 'asha',
    city: 'Bangalore',
    area: null,
    bio: null,
    phone_e164: '+919876543210',
    role: 'member',
    // The API only reveals the ID once onboarding is complete.
    short_id: complete ? 'GD-7K3M9Q0' : null,
    created_at: '2026-11-01T10:00:00Z',
    onboarding: { adult: true, consents: true, phone: true, complete: true },
    ...overrides,
  }
}

export const NOT_ONBOARDED = { adult: true, consents: true, phone: false, complete: false }

export function makeQr(overrides: Partial<QrCode> = {}): QrCode {
  return {
    short_id: '7K3M9Q0',
    display_id: 'GD-7K3M9Q0',
    token: '7K3M9Q0.123.abc',
    scan_url: 'http://localhost:5173/scan/7K3M9Q0.123.abc',
    expires_at: '2026-11-11T12:00:00Z',
    refresh_after_seconds: 30,
    ...overrides,
  }
}

/** Handlers for a signed-in session (cookie refresh works, /users/me returns `me`). */
export function signedIn(me: Me): Record<string, Handler> {
  return {
    'POST /auth/refresh': { body: TOKEN },
    'GET /users/me': { body: me },
  }
}

/** Render the whole app (routes, auth, query client) at `path`. */
export function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const result = render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { ...result, router, queryClient }
}
