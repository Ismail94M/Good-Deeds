import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router'
import { focusRing } from '../feed/feedUi'
import { RANKING_RULE, rankText } from './ranks'
import type { RegionKind, UserRank } from './scoringApi'
import { useUserRanks } from './scoringQueries'
import { formatMonth } from './scoringText'

/** The four ranking levels, as people say them (the board tabs say "Country"). */
const RANK_NAME: Record<RegionKind, string> = {
  ward: 'Area rank',
  city: 'City rank',
  state: 'State rank',
  country: 'National rank',
}

/**
 * "View all rankings" and the panel it opens: Area, City, State and National rank this month,
 * each from the same snapshots as its leaderboard (D76). Open levels show the real rank and link
 * to the board; a person on no board of a level reads "Not ranked yet this month"; levels not
 * open yet read "Coming soon". Never a made-up number, never the registered area.
 */
/** `title` heads the panel: "Your rankings", "Kavya Reddy’s rankings". */
export function RankingsButton({ handle, title }: { handle: string; title: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className={`inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-[15px] font-semibold text-brand-700 hover:border-slate-300 hover:bg-slate-50 ${focusRing}`}
      >
        <PodiumIcon />
        View all rankings
      </button>
      {open && <RankingsDialog handle={handle} title={title} onClose={() => setOpen(false)} />}
    </>
  )
}

function RankingsDialog({
  handle,
  title,
  onClose,
}: {
  handle: string
  title: string
  onClose: () => void
}) {
  const ranks = useUserRanks(handle)
  const titleId = useId()
  const panel = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    return () => opener?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab' || !panel.current) return
    // Keep focus inside the panel.
    const items = panel.current.querySelectorAll<HTMLElement>('a[href], button')
    const first = items[0]
    const last = items[items.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 sm:items-center sm:p-6"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        className="flex max-h-[90dvh] w-full flex-col gap-4 overflow-y-auto rounded-t-3xl bg-white p-5 pb-[max(20px,env(safe-area-inset-bottom))] shadow-2xl sm:max-w-md sm:rounded-3xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-0.5">
            <h2 id={titleId} className="text-lg font-extrabold tracking-tight text-slate-900">
              {title}
            </h2>
            {ranks.data && (
              <p className="text-sm text-slate-600">
                {formatMonth(ranks.data.month)} · {ranks.data.final ? 'Final' : 'Provisional'}
              </p>
            )}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close rankings"
            className={`flex size-11 shrink-0 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-800 ${focusRing}`}
          >
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              className="size-5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        {ranks.isPending ? (
          <p className="py-6 text-center text-slate-600" aria-busy="true">
            Loading rankings…
          </p>
        ) : ranks.isError ? (
          <p role="alert" className="py-6 text-center text-slate-700">
            Rankings didn’t load. Please try again.
          </p>
        ) : (
          <ul aria-label="Rankings" className="flex flex-col gap-2">
            {ranks.data.levels.map((rank) => (
              <RankRow key={rank.level} rank={rank} month={ranks.data.month} />
            ))}
          </ul>
        )}
        <p className="text-[13px] leading-relaxed text-slate-600">
          {RANKING_RULE} “=3” means a shared place.
        </p>
      </div>
    </div>
  )
}

const TONE: Record<RegionKind, string> = {
  ward: 'bg-amber-50 text-amber-700',
  city: 'bg-indigo-50 text-indigo-700',
  state: 'bg-slate-100 text-slate-400',
  country: 'bg-slate-100 text-slate-400',
}

function RankRow({ rank, month }: { rank: UserRank; month: string }) {
  const label = RANK_NAME[rank.level]
  const ranked = rank.enabled && rank.rank !== null && rank.region !== null
  const body = (
    <>
      <span
        aria-hidden
        className={`flex size-10 shrink-0 items-center justify-center rounded-xl ${TONE[rank.level]}`}
      >
        <LevelIcon kind={rank.level} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-[15px] font-semibold text-slate-900">{label}</span>
        <span className="truncate text-[13px] text-slate-600">
          {!rank.enabled
            ? 'Not open yet'
            : ranked
              ? `${rank.region?.name}${rank.shared ? ' · shared place' : ''}`
              : 'Not ranked yet this month'}
        </span>
      </span>
      {!rank.enabled ? (
        <span className="shrink-0 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-bold tracking-wide text-slate-500 uppercase">
          Coming soon
        </span>
      ) : ranked ? (
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="text-2xl font-extrabold tracking-tight text-slate-900 tabular-nums">
            {rankText(rank)}
          </span>
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-4 text-slate-400"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M9 6l6 6-6 6" />
          </svg>
        </span>
      ) : (
        <span className="shrink-0 text-xl font-bold text-slate-400" aria-hidden>
          –
        </span>
      )}
    </>
  )
  const box = 'flex min-h-16 items-center gap-3 rounded-2xl border px-3.5 py-2.5'
  return (
    <li data-testid={`rank-${rank.level}`}>
      {ranked && rank.region ? (
        <Link
          to={`/leaderboard/${encodeURIComponent(rank.region.code)}?month=${month}`}
          aria-label={`${label} ${rankText(rank)} in ${rank.region.name}${rank.shared ? ', shared' : ''}. Open the leaderboard`}
          className={`${box} border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50 ${focusRing}`}
        >
          {body}
        </Link>
      ) : (
        <div
          className={`${box} ${rank.enabled ? 'border-slate-200 bg-white' : 'border-dashed border-slate-300 bg-slate-50'}`}
        >
          {body}
        </div>
      )}
    </li>
  )
}

const iconProps = {
  'aria-hidden': true,
  viewBox: '0 0 24 24',
  className: 'size-5',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const

function LevelIcon({ kind }: { kind: RegionKind }) {
  if (kind === 'ward') {
    // A map pin: the neighbourhood.
    return (
      <svg {...iconProps}>
        <path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0C18.5 15.4 12 21 12 21z" />
        <circle cx="12" cy="10" r="2.4" />
      </svg>
    )
  }
  if (kind === 'city') {
    return (
      <svg {...iconProps}>
        <path d="M3.5 20.5h17M5 20.5V9l5-3v14.5M10 20.5V4h7v16.5M17 9.5h2v11" />
        <path d="M13 7.5h1M13 10.5h1M13 13.5h1M7 11.5h1M7 14.5h1" />
      </svg>
    )
  }
  if (kind === 'state') {
    return (
      <svg {...iconProps}>
        <path d="M4 6.5l5-2 6 2 5-2v13l-5 2-6-2-5 2z" />
        <path d="M9 4.5v13M15 6.5v13" />
      </svg>
    )
  }
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.6 2.5 3.8 5.3 3.8 8.5s-1.2 6-3.8 8.5c-2.6-2.5-3.8-5.3-3.8-8.5s1.2-6 3.8-8.5z" />
    </svg>
  )
}

export function PodiumIcon({ className = 'size-[18px]' }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9 20.5V9h6v11.5M3.5 20.5V13H9M15 15.5h5.5v5M2.5 20.5h19" />
      <path d="M12 3.5v2.5" />
    </svg>
  )
}
