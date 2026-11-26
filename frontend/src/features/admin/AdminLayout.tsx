import { useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { useAuth } from '../auth/authContext'
import { focusRing } from './adminUi'

/** Review links for moderators and admins (M3), then the admin-only M5 links (D90). */
const REVIEW_LINKS = [
  { to: '/admin/photo-reports', label: 'Photo reports' },
  { to: '/admin/comment-reports', label: 'Comment reports' },
]
const ADMIN_LINKS = [
  { to: '/admin/fraud', label: 'Fraud cases' },
  { to: '/admin/appeals', label: 'Appeals' },
  { to: '/admin/scoring/override-requests', label: 'Override requests' },
  { to: '/admin/audit', label: 'Audit log' },
]

/** Dark "ADMIN" bar with the review nav, above every `/admin` page. */
export function AdminLayout() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const navRef = useRef<HTMLElement>(null)
  const links = user?.role === 'admin' ? [...REVIEW_LINKS, ...ADMIN_LINKS] : REVIEW_LINKS
  const link = ({ isActive }: { isActive: boolean }) =>
    `inline-flex min-h-11 items-center rounded-lg px-3.5 text-sm font-semibold whitespace-nowrap ${focusRing} ${
      isActive ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-800 hover:text-white'
    }`

  // On phones the nav is one sideways-scrolling row: keep the current page's link in view.
  useEffect(() => {
    const nav = navRef.current
    const active = nav?.querySelector<HTMLElement>('[aria-current="page"]')
    if (!nav || !active || nav.scrollWidth <= nav.clientWidth) return
    const left = active.offsetLeft - nav.offsetLeft
    if (left < nav.scrollLeft || left + active.offsetWidth > nav.scrollLeft + nav.clientWidth) {
      nav.scrollLeft = left - (nav.clientWidth - active.offsetWidth) / 2
    }
  }, [pathname])

  return (
    <>
      <div className="border-b border-slate-800 bg-slate-900 text-white">
        <div className="mx-auto flex max-w-6xl items-center gap-x-3 px-4 py-1.5 sm:flex-wrap sm:gap-x-4 sm:gap-y-1 lg:px-6">
          <span className="shrink-0 rounded-md bg-slate-700 px-2 py-0.5 text-xs font-semibold tracking-[0.04em]">
            ADMIN
          </span>
          {/* Phones: one row that scrolls sideways (the six admin links would wrap to three
              rows) and runs to the screen edge so the cut-off link shows there is more; wider screens
              wrap. The padding keeps focus outlines inside the scroller. */}
          <nav
            ref={navRef}
            aria-label="Admin"
            className="-my-1 -mr-4 ml-auto min-w-0 overflow-x-auto sm:my-0 sm:mr-0 sm:overflow-visible"
          >
            <ul className="flex gap-1 py-1 pr-4 pl-1 sm:flex-wrap sm:justify-end sm:p-0">
              {links.map((item) => (
                <li key={item.to} className="shrink-0">
                  <NavLink to={item.to} className={link}>
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </div>
      <Outlet />
    </>
  )
}
