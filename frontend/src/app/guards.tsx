import { useEffect } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router'
import { Loading } from '../components/ui'
import { useAuth } from '../features/auth/authContext'
import type { Me } from '../features/auth/identityApi'
import { safeNext } from '../features/auth/validation'
import { setRescanNotice } from '../features/report/rescanNotice'
import { NotFoundPage } from './NotFoundPage'

/**
 * The `next` for a login or onboarding redirect. A `/scan/:token` link never puts its token in
 * `next` (R5-L2): those witnesses come back to the Record a deed hub, which asks for a new code.
 */
function useHere() {
  const location = useLocation()
  if (location.pathname.startsWith('/scan/')) return encodeURIComponent('/report')
  return encodeURIComponent(location.pathname + location.search)
}

/** Remember to show the hub's "ask for a new code" notice after a `/scan/:token` redirect. */
function useRescanNotice(redirecting: boolean) {
  const { pathname } = useLocation()
  useEffect(() => {
    if (redirecting && pathname.startsWith('/scan/')) setRescanNotice()
  }, [redirecting, pathname])
}

/** Only for signed-in users; others go to /login and come back afterwards. */
export function RequireAuth() {
  const { user, isLoading } = useAuth()
  const here = useHere()
  useRescanNotice(!isLoading && !user)
  if (isLoading) return <Loading />
  if (!user) return <Navigate to={`/login?next=${here}`} replace />
  return <Outlet />
}

/** Only for users who finished onboarding (adult, consents, verified phone). */
export function RequireOnboarded() {
  const { user, isLoading } = useAuth()
  const here = useHere()
  useRescanNotice(!isLoading && (!user || !user.onboarding.complete))
  if (isLoading) return <Loading />
  if (!user) return <Navigate to={`/login?next=${here}`} replace />
  if (!user.onboarding.complete) return <Navigate to={`/onboarding?next=${here}`} replace />
  return <Outlet />
}

/** Login and sign-up pages: send already signed-in users onwards. */
export function RedirectIfAuthed() {
  const { user, isLoading } = useAuth()
  const location = useLocation()
  if (isLoading) return <Loading />
  if (user) {
    const next = new URLSearchParams(location.search).get('next')
    if (user.onboarding.complete) return <Navigate to={safeNext(next, '/')} replace />
    const query = next ? `?next=${encodeURIComponent(safeNext(next))}` : ''
    return <Navigate to={`/onboarding${query}`} replace />
  }
  return <Outlet />
}

export type Role = Me['role']

/**
 * Only for signed-in users with one of `roles` (M3 admin pages: moderator or admin, Q34).
 * Signed-out visitors go to /login and come back; anyone signed in without the role sees the
 * NotFound page (§6.3), so the admin area isn't advertised. The API checks the role again.
 */
export function RequireRole({ roles }: { roles: readonly Role[] }) {
  const { user, isLoading } = useAuth()
  const here = useHere()
  if (isLoading) return <Loading />
  if (!user) return <Navigate to={`/login?next=${here}`} replace />
  if (!roles.includes(user.role)) return <NotFoundPage />
  return <Outlet />
}
