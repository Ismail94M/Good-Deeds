/**
 * QA4: route guards per M2-DESIGN §5.2, and the header (D25) on the M2 pages.
 */

import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../test/utils'

const notOnboarded = () => makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })
const siteHeader = () => screen.getAllByRole('banner')[0]

/** §5.2: every M2 route with its guard. */
const ONBOARDED_ROUTES = [
  '/me/qr',
  '/report',
  '/report/scan',
  '/report/code',
  '/scan/7K3M9Q0.123.abc',
  '/report/scan/11111111-2222-3333-4444-555555555555',
  '/me/deeds/d1/edit',
  '/me/self-logs',
  '/me/self-logs/new',
  '/me/self-logs/log-1/edit',
]
const AUTH_ROUTES = ['/me/deeds', '/me/deeds?tab=reported', '/me/deeds/d1']

/** R5-L2: a `/scan/:token` link never puts its token in `next`; it comes back to the hub. */
const expectedNext = (path: string) => (path.startsWith('/scan/') ? '/report' : path)

describe('§5.2 guards: RequireOnboarded routes', () => {
  it.each(ONBOARDED_ROUTES)('%s sends a signed-out visitor to login with next', async (path) => {
    mockApi()
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(expectedNext(path))}`)
  })

  it.each(ONBOARDED_ROUTES)('%s sends a user who is not onboarded to onboarding', async (path) => {
    mockApi(signedIn(notOnboarded()))
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(expectedNext(path))}`)
  })
})

describe('§5.2 guards: RequireAuth routes', () => {
  it.each(AUTH_ROUTES)('%s sends a signed-out visitor to login with next', async (path) => {
    mockApi()
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(path)}`)
  })

  it.each(AUTH_ROUTES)('%s stays open to a user who is not onboarded', async (path) => {
    mockApi({
      ...signedIn(notOnboarded()),
      'GET /me/deeds': { body: { items: [], next_cursor: null } },
      'GET /deeds/d1': { status: 404, body: { detail: 'Not found', code: 'deed_not_found' } },
      'GET /deeds/options': {
        status: 403,
        body: { detail: 'Finish setting up.', code: 'onboarding_required' },
      },
    })
    const { router } = renderApp(path)
    // "My deeds" (h1) or "This deed isn’t available" (the 404 state): the page itself.
    expect(
      await screen.findByRole('heading', { name: /^(My deeds|This deed isn’t available)$/ }),
    ).toBeVisible()
    expect(router.state.location.pathname + router.state.location.search).toBe(path)
  })
})

describe('§5.2: routes that M2 does not add (D26)', () => {
  it.each(['/report/invite', '/invite/sometoken', '/me/invites'])(
    '%s is the not-found page',
    async (path) => {
      mockApi(signedIn(makeMe()))
      const { router } = renderApp(path)
      // Not redirected anywhere: the catch-all page answers.
      expect(await screen.findByRole('heading', { level: 1 })).toBeVisible()
      expect(router.state.location.pathname).toBe(path)
      expect(screen.queryByText(/invite/i, { selector: 'h1' })).not.toBeInTheDocument()
    },
  )
})

describe('header on M2 pages (D25)', () => {
  it.each([
    ['/me/qr', 'My QR'],
    ['/profile', 'Profile'],
  ])('marks %s as the current nav item', async (path, name) => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /users/me/qr': { status: 500, body: {} },
    })
    renderApp(path)
    const nav = within(siteHeader()).getByRole('navigation', { name: 'Main' })
    await waitFor(() =>
      expect(within(nav).getByRole('link', { name })).toHaveAttribute('aria-current', 'page'),
    )
    const others = within(nav)
      .getAllByRole('link')
      .filter((link) => link.textContent !== name)
    for (const link of others) expect(link).not.toHaveAttribute('aria-current')
  })

  it.each(['/me/deeds', '/me/self-logs', '/report', '/me/qr'])(
    '%s has no Log out in the header',
    async (path) => {
      mockApi({
        ...signedIn(makeMe()),
        'GET /me/deeds': { body: { items: [], next_cursor: null } },
        'GET /me/self-logs': { body: { items: [], next_cursor: null } },
      })
      renderApp(path)
      const header = siteHeader()
      expect(await within(header).findByRole('link', { name: 'Record a deed' })).toBeVisible()
      expect(within(header).queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()
      expect(within(header).queryByRole('link', { name: /log out/i })).not.toBeInTheDocument()
    },
  )

  it('gives every header control a 44 px target', async () => {
    mockApi(signedIn(makeMe()))
    renderApp('/profile')
    const header = siteHeader()
    await within(header).findByRole('link', { name: 'Record a deed' })
    for (const link of within(header).getAllByRole('link')) {
      expect(link.className).toMatch(/\bmin-h-1[1-3]\b/)
    }
  })
})
