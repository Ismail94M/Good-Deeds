import { NavLink, Outlet } from 'react-router'
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
  const links = user?.role === 'admin' ? [...REVIEW_LINKS, ...ADMIN_LINKS] : REVIEW_LINKS
  const link = ({ isActive }: { isActive: boolean }) =>
    `inline-flex min-h-11 items-center rounded-lg px-3.5 text-sm font-semibold whitespace-nowrap ${focusRing} ${
      isActive ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-800 hover:text-white'
    }`
  return (
    <>
      <div className="border-b border-slate-800 bg-slate-900 text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 lg:px-6">
          <span className="rounded-md bg-slate-700 px-2 py-0.5 text-xs font-semibold tracking-[0.04em]">
            ADMIN
          </span>
          <nav aria-label="Admin" className="ml-auto min-w-0">
            <ul className="flex flex-wrap justify-end gap-1">
              {links.map((item) => (
                <li key={item.to}>
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
