import { Navigate, Outlet, useLocation } from 'react-router'
import { Loading } from '../components/ui'
import { useAuth } from '../features/auth/authContext'
import { safeNext } from '../features/auth/validation'

function useHere() {
  const location = useLocation()
  return encodeURIComponent(location.pathname + location.search)
}

/** Only for signed-in users; others go to /login and come back afterwards. */
export function RequireAuth() {
  const { user, isLoading } = useAuth()
  const here = useHere()
  if (isLoading) return <Loading />
  if (!user) return <Navigate to={`/login?next=${here}`} replace />
  return <Outlet />
}

/** Only for users who finished onboarding (adult, consents, verified phone). */
export function RequireOnboarded() {
  const { user, isLoading } = useAuth()
  const here = useHere()
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
