import { Link, NavLink, Outlet, ScrollRestoration, useLocation } from 'react-router'
import { LinkButton, LogoMark } from '../components/ui'
import { useAuth } from '../features/auth/authContext'
import { Avatar } from '../features/media/avatar/Avatar'
import { NotificationBell } from '../features/notifications/NotificationBell'
import { useReviewsUnread } from '../features/reviews/reviewsApi'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

function navClass({ isActive }: { isActive: boolean }) {
  return `flex min-h-11 items-center justify-center rounded-lg px-2 text-sm font-medium sm:px-3 ${focusRing} ${
    isActive ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-100'
  }`
}

const YEAR = new Date().getFullYear()

/** `/d/abc` → `/deeds/abc`: after signing in from a shared link, open the member page. */
function sharedDeedNext(pathname: string): string {
  return `/deeds/${pathname.slice('/d/'.length)}`
}

const headerLink = `inline-flex min-h-11 items-center gap-1 font-semibold text-brand-700 hover:text-brand-900 ${focusRing}`

function PlusIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-4.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

/** M5 §6.2: a dot on Profile while the person has unseen review items (`GET /reviews` unread). */
function ReviewsDot() {
  const unread = useReviewsUnread()
  if (!unread) return null
  return (
    <>
      <span aria-hidden className="ml-1.5 size-2 shrink-0 rounded-full bg-red-600" />
      <span className="sr-only"> (new review update)</span>
    </>
  )
}

/**
 * Header (M2-DESIGN D25). Signed in: logo, nav Home · Leaderboard · My QR · Profile (M4 §6.2), and a blue "Record a deed"
 * button on every page; on phones the logo and button share the first row and the nav runs full
 * width below. Log out lives on the Profile page. Signed out: the sign-in / sign-up link.
 */
export function Layout() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  // `/scan/:token` is the camera-app entry into Record a deed.
  const onReport =
    pathname === '/report' || pathname.startsWith('/report/') || pathname.startsWith('/scan/')

  return (
    <div className="flex min-h-svh flex-col">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:m-2 focus:rounded focus:bg-white focus:p-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-10 border-b border-slate-200/70 bg-canvas/90 backdrop-blur">
        <div
          className={`mx-auto flex max-w-6xl flex-wrap items-center px-4 py-2 lg:px-6 ${
            user ? 'gap-x-4 gap-y-1' : 'justify-between gap-x-2'
          }`}
        >
          <Link
            to="/"
            className={`order-1 flex min-h-11 items-center gap-2.5 text-lg font-bold tracking-tight text-slate-900 ${focusRing}`}
          >
            <LogoMark />
            Good Deeds
          </Link>
          <nav
            aria-label="Main"
            className={user ? 'order-3 w-full sm:order-2 sm:ml-auto sm:w-auto' : 'order-2'}
          >
            {user ? (
              <ul className="grid grid-cols-4 gap-1 sm:flex">
                <li>
                  <NavLink to="/" end className={navClass}>
                    Home
                  </NavLink>
                </li>
                <li>
                  {/* M4 §6.2: the city board; ward boards are reached from there. */}
                  <NavLink to="/leaderboard" className={navClass}>
                    Leaderboard
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/me/qr" className={navClass}>
                    My QR
                  </NavLink>
                </li>
                <li>
                  <NavLink to="/profile" className={navClass}>
                    {/* M6 §6.2: my small avatar before "Profile". */}
                    <Avatar user={user} size="nav" className="mr-1 sm:mr-1.5" />
                    Profile
                    <ReviewsDot />
                  </NavLink>
                </li>
              </ul>
            ) : pathname.startsWith('/d/') ? (
              // Shared deed page (F7 §3): Sign in and Join, both returning to the deed.
              <div className="flex items-center gap-1">
                <Link
                  to={`/login?next=${encodeURIComponent(sharedDeedNext(pathname))}`}
                  className={`${headerLink} px-3`}
                >
                  Sign in
                </Link>
                <LinkButton
                  to={`/signup?next=${encodeURIComponent(sharedDeedNext(pathname))}`}
                  className="px-4 text-[15px]"
                >
                  Join
                </LinkButton>
              </div>
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
          {user && (
            <div className="order-2 ml-auto flex items-center gap-1.5 sm:order-3 sm:ml-0">
              {/* M6 §6.3: the bell sits beside "Record a deed". */}
              <NotificationBell />
              <LinkButton
                to="/report"
                aria-current={onReport ? 'page' : undefined}
                className="gap-1.5 px-4 text-[15px]"
              >
                <PlusIcon />
                Record a deed
              </LinkButton>
            </div>
          )}
        </div>
      </header>
      <div id="content" className="flex flex-1 flex-col">
        <Outlet />
      </div>
      <footer className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-6 text-sm text-slate-500 lg:px-6">
        <p>© {YEAR} Good Deeds. All rights reserved.</p>
        <div className="flex gap-5">
          <Link
            to="/privacy"
            className={`inline-flex min-h-11 items-center hover:text-slate-800 ${focusRing}`}
          >
            Privacy
          </Link>
          <Link
            to="/terms"
            className={`inline-flex min-h-11 items-center hover:text-slate-800 ${focusRing}`}
          >
            Terms
          </Link>
        </div>
      </footer>
      <ScrollRestoration />
    </div>
  )
}
