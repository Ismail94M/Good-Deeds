import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../test/utils'
import { makeCommentReportDetail, makePhotoReportDetail } from '../features/admin/testFixtures'

describe('route guards', () => {
  it('sends logged-out visitors to login and remembers where they were going', async () => {
    mockApi()
    const { router } = renderApp('/me/qr')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe('?next=%2Fme%2Fqr')
    expect(
      await screen.findByRole('heading', { name: 'Small acts. A world of good.' }),
    ).toBeInTheDocument()
  })

  it('protects the profile page', async () => {
    mockApi()
    const { router } = renderApp('/profile')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  })

  it('sends signed-in users without a verified phone to onboarding', async () => {
    mockApi(signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })))
    const { router } = renderApp('/me/qr')
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding'))
    expect(router.state.location.search).toBe('?next=%2Fme%2Fqr')
  })

  it('lets onboarding-incomplete users see their profile', async () => {
    mockApi(signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })))
    renderApp('/profile')
    expect(await screen.findByRole('heading', { name: 'Asha' })).toBeInTheDocument()
  })

  it('redirects signed-in users away from login', async () => {
    mockApi(signedIn(makeMe()))
    const { router } = renderApp('/login')
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
  })

  it('sends a finished user from onboarding to their QR code', async () => {
    mockApi({ ...signedIn(makeMe()), 'GET /users/me/qr': { status: 500, body: {} } })
    const { router } = renderApp('/onboarding')
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/qr'))
  })
})

describe('RequireRole (M3 admin pages, Q34)', () => {
  const ADMIN_PATHS = [
    '/admin/photo-reports',
    '/admin/photo-reports/case-1',
    '/admin/comment-reports',
    '/admin/comment-reports/case-1',
  ]

  it.each(ADMIN_PATHS)('%s sends a signed-out visitor to login with next', async (path) => {
    mockApi()
    const { router } = renderApp(path)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent(path)}`)
  })

  it.each(ADMIN_PATHS)('%s shows NotFound to a member', async (path) => {
    mockApi(signedIn(makeMe({ role: 'member' })))
    const { router } = renderApp(path)
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe(path)
  })

  it.each([
    ['moderator', '/admin/photo-reports', 'Photo reports'],
    ['admin', '/admin/photo-reports/case-1', 'Review photo report'],
    ['moderator', '/admin/comment-reports', 'Comment reports'],
    ['admin', '/admin/comment-reports/case-1', 'Review comment report'],
  ] as const)('lets a %s open %s', async (role, path, heading) => {
    mockApi({
      ...signedIn(makeMe({ role })),
      'GET /admin/photo-reports/case-1': { body: makePhotoReportDetail() },
      'GET /admin/comment-reports/case-1': { body: makeCommentReportDetail() },
    })
    renderApp(path)
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Page not found' })).toBeNull()
  })

  it('also checks onboarding-incomplete moderators by role only', async () => {
    mockApi(signedIn(makeMe({ role: 'moderator', onboarding: NOT_ONBOARDED })))
    renderApp('/admin/photo-reports')
    expect(await screen.findByRole('heading', { name: 'Photo reports' })).toBeInTheDocument()
  })
})
