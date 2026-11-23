import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../lib/api'
import { Avatar, focusRing, secondaryButton } from '../feed/feedUi'
import type { FeaturedCityBoard, FeaturedRow } from './scoringApi'
import { useFeaturedCity } from './scoringQueries'
import { deedsText, formatMonth, rankText } from './scoringText'

/**
 * "Featured City Top Performers" (PO 2026-11-23, M4-DESIGN D72): the top members registered in
 * the featured city this month, ranked by the board rules. A podium for the first three, then a
 * short list. The city comes from config (the API default is the first enabled featured city);
 * `city` picks another, so more cities or a city switcher need no change here. Renders nothing
 * when no city is featured. Ranking and points are the server's; nothing is computed here.
 */
export function FeaturedCity({
  city = null,
  month,
}: {
  city?: string | null
  month: string | null
}) {
  const featured = useFeaturedCity(city, month)
  if (featured.isError) {
    if (featured.error instanceof ApiError && featured.error.status === 404) return null
    return (
      <Frame title="Featured City Top Performers">
        <p className="text-slate-700">We couldn’t load the top performers.</p>
        <button
          type="button"
          className={`${secondaryButton} self-start`}
          onClick={() => void featured.refetch()}
        >
          Try again
        </button>
      </Frame>
    )
  }
  if (!featured.data) {
    return (
      <Frame title="Featured City Top Performers">
        <p role="status" className="text-slate-600">
          Loading top performers…
        </p>
      </Frame>
    )
  }
  return <Board board={featured.data} />
}

function Board({ board }: { board: FeaturedCityBoard }) {
  const podium = board.items.slice(0, 3)
  const rest = board.items.slice(3)
  const status = board.final ? 'Final' : 'Provisional'
  return (
    <Frame
      title="Featured City Top Performers"
      subtitle={`${board.city.name} · ${formatMonth(board.month)} · ${status}`}
    >
      {board.items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-amber-300 bg-white/70 px-4 py-6 text-center text-slate-600">
          No one from {board.city.name} is ranked yet this month. Deeds verified by a witness will
          put people here.
        </p>
      ) : (
        <>
          <ol
            aria-label={`Top performers in ${board.city.name}`}
            className="grid grid-cols-3 items-end gap-2"
          >
            {podium.map((row, i) => (
              <PodiumPlace key={row.user.handle ?? `p-${i}`} row={row} position={i} />
            ))}
          </ol>
          {rest.length > 0 && (
            <ol
              start={4}
              aria-label={`More top performers in ${board.city.name}`}
              className="flex flex-col divide-y divide-amber-100 rounded-2xl border border-amber-200 bg-white"
            >
              {rest.map((row, i) => (
                <li key={row.user.handle ?? `r-${i}`}>
                  <ListRow row={row} />
                </li>
              ))}
            </ol>
          )}
        </>
      )}
      <p className="text-sm text-slate-600">
        People registered in {board.city.name}, ranked like every board: verified deeds this month,
        then points. “=2” means a shared place.
      </p>
    </Frame>
  )
}

function Frame({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: ReactNode
}) {
  return (
    <section
      aria-labelledby="featured-city-h"
      className="flex flex-col gap-4 rounded-3xl border border-amber-200 bg-gradient-to-b from-amber-50 via-white to-white p-4 shadow-sm"
    >
      <header className="flex items-start gap-3">
        <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-amber-400 text-white shadow-sm">
          <TrophyIcon />
        </span>
        <div className="flex min-w-0 flex-col">
          <h2 id="featured-city-h" className="text-lg leading-tight font-bold text-slate-900">
            {title}
          </h2>
          {subtitle && <p className="text-sm font-medium text-amber-900">{subtitle}</p>}
        </div>
      </header>
      {children}
    </section>
  )
}

/** Visual podium order is 2 · 1 · 3; the list itself stays in rank order for screen readers. */
const PLACE_STYLE = [
  {
    order: 'order-2',
    block: 'h-24 bg-amber-400 text-white',
    ring: 'ring-amber-400',
    medal: 'Gold',
  },
  {
    order: 'order-1',
    block: 'h-16 bg-slate-300 text-slate-800',
    ring: 'ring-slate-300',
    medal: 'Silver',
  },
  {
    order: 'order-3',
    block: 'h-12 bg-orange-300 text-orange-950',
    ring: 'ring-orange-300',
    medal: 'Bronze',
  },
] as const

function PodiumPlace({ row, position }: { row: FeaturedRow; position: number }) {
  const style = PLACE_STYLE[position]
  const first = position === 0
  return (
    <li
      data-testid="podium-place"
      className={`flex min-w-0 flex-col items-center gap-1.5 ${style.order}`}
    >
      <span className={`rounded-full ring-4 ${style.ring}`}>
        <Avatar
          name={row.user.display_name}
          handle={row.user.handle ?? ''}
          size={first ? 'xl' : 'lg'}
        />
      </span>
      <Name row={row} className="w-full text-center text-sm" />
      <span className="text-center text-xs leading-snug text-slate-700">
        <Stats row={row} />
      </span>
      <span
        className={`flex w-full flex-col items-center justify-start rounded-t-xl pt-2 font-bold shadow-inner ${style.block}`}
      >
        <span
          aria-label={row.shared ? `Rank ${row.rank}, shared` : `Rank ${row.rank}`}
          className="text-2xl tabular-nums"
        >
          {rankText(row)}
        </span>
        <span className="text-[11px] font-semibold tracking-wide uppercase opacity-80">
          {style.medal}
        </span>
      </span>
    </li>
  )
}

function ListRow({ row }: { row: FeaturedRow }) {
  return (
    <div data-testid="featured-row" className="flex items-center gap-3 px-3.5 py-3">
      <span
        aria-label={row.shared ? `Rank ${row.rank}, shared` : `Rank ${row.rank}`}
        className="w-9 shrink-0 text-center text-lg font-bold text-slate-900 tabular-nums"
      >
        {rankText(row)}
      </span>
      <Avatar name={row.user.display_name} handle={row.user.handle ?? ''} size="md" />
      <Name row={row} className="min-w-0 flex-1" />
      <span className="shrink-0 text-right text-sm text-slate-700">
        <Stats row={row} />
      </span>
    </div>
  )
}

function Name({ row, className }: { row: FeaturedRow; className: string }) {
  if (row.user.handle === null) {
    return (
      <span className={`truncate font-semibold text-slate-600 ${className}`}>
        {row.user.display_name}
      </span>
    )
  }
  return (
    <Link
      to={`/u/${encodeURIComponent(row.user.handle)}`}
      className={`truncate font-semibold text-slate-900 underline-offset-2 hover:underline ${focusRing} ${className}`}
    >
      {row.user.display_name}
    </Link>
  )
}

/** "40 points · 4 deeds" (points only when the API sends them). */
function Stats({ row }: { row: FeaturedRow }) {
  return (
    <>
      {row.points !== null && (
        <>
          <span className="font-semibold text-slate-900">{pointsText(row.points)}</span>
          <br />
        </>
      )}
      {deedsText(row.deed_count)}
    </>
  )
}

function pointsText(points: number): string {
  return `${points} ${points === 1 ? 'point' : 'points'}`
}

function TrophyIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-5.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM17 6h3a3 3 0 0 1-3 4M7 6H4a3 3 0 0 0 3 4" />
    </svg>
  )
}
