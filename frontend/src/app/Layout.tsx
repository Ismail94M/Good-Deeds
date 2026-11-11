import { useState } from 'react'
import { Link, NavLink, Outlet, ScrollRestoration, useNavigate } from 'react-router'
import { useAuth } from '../features/auth/authContext'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

function navClass({ isActive }: { isActive: boolean }) {
  return `flex min-h-11 items-center justify-center rounded-full px-3 text-sm font-medium ${focusRing} ${
    isActive ? 'bg-brand-100 text-brand-900' : 'text-stone-700 hover:bg-stone-100'
  }`
}

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
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
      <header className="sticky top-0 z-10 border-b border-stone-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-x-2 px-4 py-2">
          <Link
            to="/"
            className={`flex min-h-11 items-center text-lg font-bold text-brand-700 ${focusRing}`}
          >
            Good Deeds
          </Link>
          <nav aria-label="Main" className="w-full sm:w-auto">
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
            ) : (
              <ul className="flex justify-end gap-1">
                <li>
                  <NavLink to="/login" className={navClass}>
                    Log in
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/signup" className={navClass}>
                    Sign up
                  </NavLink>
                </li>
              </ul>
            )}
          </nav>
        </div>
      </header>
      <div id="content" className="flex flex-1 flex-col">
        <Outlet />
      </div>
      <footer className="mx-auto flex w-full max-w-xl gap-4 px-4 py-6 text-sm text-stone-500">
        <Link to="/terms" className={`underline ${focusRing}`}>
          Terms
        </Link>
        <Link to="/privacy" className={`underline ${focusRing}`}>
          Privacy
        </Link>
      </footer>
      <ScrollRestoration />
    </div>
  )
}
