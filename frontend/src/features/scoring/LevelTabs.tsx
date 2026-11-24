import { Link } from 'react-router'
import { focusRing, LockIcon } from '../feed/feedUi'
import { comingSoonText, LEVEL_LABEL, LEVEL_ORDER, levelHref } from './levels'
import type { RegionKind } from './scoringApi'

/**
 * Area · City · State · Country. Open levels are links; locked ones are visible but inert, with a
 * lock icon and a "Locked" label, so people can see what is coming. Which levels are open comes
 * from config (`open`), never from this file.
 */
export function LevelTabs({
  active,
  open,
  month,
}: {
  active: RegionKind | null
  open: Set<RegionKind>
  month: string | null
}) {
  const locked = LEVEL_ORDER.filter((kind) => !open.has(kind))
  return (
    <div className="flex flex-col gap-2">
      <nav aria-label="Leaderboard levels">
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {LEVEL_ORDER.map((kind) => (
            <li key={kind} className="flex">
              {open.has(kind) ? (
                <Link
                  to={levelHref(kind, month)}
                  aria-current={active === kind ? 'page' : undefined}
                  className={`inline-flex min-h-11 w-full items-center justify-center rounded-xl px-3 text-[15px] font-semibold ${focusRing} ${
                    active === kind
                      ? 'bg-brand-700 text-white shadow-sm'
                      : 'border border-slate-200 bg-white text-slate-800 hover:bg-slate-50'
                  }`}
                >
                  {LEVEL_LABEL[kind]}
                </Link>
              ) : (
                <span
                  role="link"
                  aria-disabled="true"
                  aria-label={`${LEVEL_LABEL[kind]}, locked`}
                  title={`${LEVEL_LABEL[kind]} leaderboards are coming soon`}
                  data-testid={`level-locked-${kind}`}
                  className="inline-flex min-h-11 w-full cursor-not-allowed items-center justify-center gap-1.5 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 text-[15px] font-semibold text-slate-400 select-none sm:flex-col sm:gap-0.5 sm:py-1"
                >
                  {/* Four in a row from `sm` leaves too little width for name and tag side by side
                      ("Country" overflowed), so they stack there. */}
                  <span className="inline-flex items-center gap-1.5">
                    <LockIcon className="size-4 shrink-0" />
                    {LEVEL_LABEL[kind]}
                  </span>
                  <span className="rounded-full bg-slate-200 px-1.5 py-0.5 text-[11px] leading-none font-bold tracking-wide text-slate-600 uppercase">
                    Locked
                  </span>
                </span>
              )}
            </li>
          ))}
        </ul>
      </nav>
      {locked.length > 0 && <p className="text-sm text-slate-600">{comingSoonText(locked)}</p>}
    </div>
  )
}
