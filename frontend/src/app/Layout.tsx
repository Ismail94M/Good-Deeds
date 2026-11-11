import { useState } from 'react'
import { Link, NavLink, Outlet, ScrollRestoration, useLocation, useNavigate } from 'react-router'
import { LogoMark } from '../components/ui'
import { useAuth } from '../features/auth/authContext'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

function navClass({ isActive }: { isActive: boolean }) {
  return `flex min-h-11 items-center justify-center rounded-lg px-3 text-sm font-medium ${focusRing} ${
    isActive ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-100'
  }`
}

const YEAR = new Date().getFullYear()

const headerLink = `inline-flex min-h-11 items-center gap-1 font-semibold text-brand-700 hover:text-brand-900 ${focusRing}`

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [loggingOut, setLoggingOut] = useState(false)

  async function handleLogout() {
    setLoggingOut(true)
    await logout()
    setLoggingOut(false)
    navigate('/', { replace: true })
  }

  return (
    <div className="flex min-h-svh flex-col">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:m-2 focus:rounded focus:bg-white focus:p-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-10 border-b border-slate-200/70 bg-canvas/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-2 px-4 py-2 lg:px-6">
          <Link
            to="/"
            className={`flex min-h-11 items-center gap-2.5 text-lg font-bold tracking-tight text-slate-900 ${focusRing}`}
          >
            <LogoMark />
            Good Deeds
          </Link>
          <nav aria-label="Main" className={user ? 'w-full sm:w-auto' : undefined}>
            {user ? (
              <ul className="grid grid-cols-4 gap-1 sm:flex">
                <li>
                  <NavLink to="/" end className={navClass}>
                    Home
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/me/qr" className={navClass}>
                    My QR
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/profile" className={navClass}>
                    Profile
                  </NavLink>
                </li>
                <li>
                  <button
                    type="button"
                    onClick={() => void handleLogout()}
                    disabled={loggingOut}
                    className={`${navClass({ isActive: false })} w-full`}
                  >
                    Log out
                  </button>
                </li>
              </ul>
            ) : pathname === '/signup' ? (
              <p className="text-sm text-slate-500">
                <span className="hidden sm:inline">Have an account? </span>
                <Link to="/login" className={headerLink}>
                  Sign in
                </Link>
              </p>
            ) : (
              <p className="text-sm text-slate-500">
                <span className="hidden sm:inline">New here? </span>
                <Link to="/signup" className={headerLink}>
                  Create an account <span aria-hidden>↗</span>
                </Link>
              </p>
            )}
          </nav>
        </div>
      </header>
      <div id="content" className="flex flex-1 flex-col">
        <Outlet />
      </div>
      <footer className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-6 text-sm text-slate-500 lg:px-6">
        <p>© {YEAR} Good Deeds. All rights reserved.</p>
        <div className="flex gap-5">
          <Link to="/privacy" className={`hover:text-slate-800 ${focusRing}`}>
            Privacy
          </Link>
          <Link to="/terms" className={`hover:text-slate-800 ${focusRing}`}>
            Terms
          </Link>
        </div>
      </footer>
      <ScrollRestoration />
    </div>
  )
}
