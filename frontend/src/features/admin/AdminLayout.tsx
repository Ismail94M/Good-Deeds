import { NavLink, Outlet } from 'react-router'
import { focusRing } from './adminUi'

/** Dark "ADMIN" bar with the review nav, above every `/admin` page. */
export function AdminLayout() {
  const link = ({ isActive }: { isActive: boolean }) =>
    `inline-flex min-h-11 items-center rounded-lg px-3.5 text-sm font-semibold ${focusRing} ${
      isActive ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-800 hover:text-white'
    }`
  return (
    <>
      <div className="border-b border-slate-800 bg-slate-900 text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 lg:px-6">
          <span className="rounded-md bg-slate-700 px-2 py-0.5 text-xs font-semibold tracking-[0.04em]">
            ADMIN
          </span>
          <nav aria-label="Admin" className="ml-auto">
            <ul className="flex gap-1">
              <li>
                <NavLink to="/admin/photo-reports" className={link}>
                  Photo reports
                </NavLink>
              </li>
              <li>
                <NavLink to="/admin/comment-reports" className={link}>
                  Comment reports
                </NavLink>
              </li>
            </ul>
          </nav>
        </div>
      </div>
      <Outlet />
    </>
  )
}
