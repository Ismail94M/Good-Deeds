import { useId, useMemo } from 'react'
import { Link, Navigate, useParams, useSearchParams } from 'react-router'
import { ErrorAlert, inputClass, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { formatWithdrawUntil } from '../deeds/withdrawWindow'
import { Avatar, FeedMain, focusRing, LockIcon, secondaryButton } from '../feed/feedUi'
import { FeaturedCity } from './FeaturedCity'
import { DEFAULT_LEVEL, LEVEL_LABEL, LEVEL_NOUN, levelHref, openLevels, parseLevel } from './levels'
import { LevelTabs } from './LevelTabs'
import { RANKING_RULE } from './ranks'
import { RegionPicker } from './RegionPicker'
import type { BoardTitle, Leaderboard, LeaderboardRow, RegionKind } from './scoringApi'
import { useHeroes, useLeaderboard, useMyScoring, useScoringOptions } from './scoringQueries'
import {
  currentIstMonth,
  deedsText,
  formatMonth,
  noHeroText,
  rankText,
  recentMonths,
} from './scoringText'
import { TierChip } from './TierChip'

/** How many months the month picker offers, the current one included. */
const MONTHS_SHOWN = 12

/**
 * `/leaderboard[?level=<kind>]` (a level's landing) and `/leaderboard/:regionCode` (one board)
 * (M4-DESIGN §6.3, F17). Level tabs Area · City · State · Country sit on top; levels the config
 * has not opened are shown locked (PO 2026-11-23: Area only for now). A level's landing opens the
 * user's own board of that level, or asks them to find a place. `?month=YYYY-MM` picks a past
 * month; without it the API shows the current IST month. Rows show rank ("=2" for a shared place),
 * name, tier and verified-deed count, never points or scores (D69). Provisional boards say so;
 * final boards list their Kindness Heroes and mark title holders. Ranking is the server's.
 */
export function LeaderboardPage() {
  const { regionCode = null } = useParams()
  const [params] = useSearchParams()
  const month = params.get('month')
  const options = useScoringOptions()
  const open = openLevels(options.data)
  if (regionCode === null) {
    const level = parseLevel(params.get('level')) ?? DEFAULT_LEVEL
    return (
      <FeedMain>
        <LevelTabs active={level} open={open} month={month} />
        {open.has(level) ? (
          <LevelLanding level={level} month={month} />
        ) : (
          <LockedLevel level={level} month={month} />
        )}
      </FeedMain>
    )
  }
  return <BoardView regionCode={regionCode} month={month} open={open} />
}

/** One board: `/leaderboard/:regionCode`. */
function BoardView({
  regionCode,
  month,
  open,
}: {
  regionCode: string
  month: string | null
  open: Set<RegionKind>
}) {
  const [params, setParams] = useSearchParams()
  const board = useLeaderboard(regionCode, month)
  const pages = board.data?.pages
  const first = pages?.[0]
  const rows = useMemo(() => pages?.flatMap((page) => page.items) ?? [], [pages])
  const level = first?.region.kind ?? null

  const current = currentIstMonth()
  const months = recentMonths(current, MONTHS_SHOWN)
  const selected = month ?? first?.month ?? current
  if (!months.includes(selected)) months.push(selected)
  const setMonth = (value: string) => {
    const next = new URLSearchParams(params)
    if (value === current) next.delete('month')
    else next.set('month', value)
    setParams(next)
  }

  return (
    <FeedMain>
      <LevelTabs active={level} open={open} month={month} />
      <MyBoards level={level ?? DEFAULT_LEVEL} regionCode={regionCode} month={month} />
      <RegionPicker kind={level ?? DEFAULT_LEVEL} month={month} />
      <FeaturedCity month={month} />
      {board.isPending ? (
        <Loading label="Loading the leaderboard…" />
      ) : board.isError && !first ? (
        <BoardError error={board.error} retry={() => void board.refetch()} />
      ) : first ? (
        <>
          <header className="flex flex-col gap-1">
            <h1 className="text-[26px] leading-tight font-bold tracking-tight text-slate-900">
              {first.region.name}
            </h1>
            <p className="text-slate-600">
              {LEVEL_LABEL[first.region.kind]} leaderboard · {formatMonth(first.month)}
            </p>
          </header>
          <MonthPicker months={months} value={selected} onChange={setMonth} />
          <StatusBanner board={first} />
          {first.final && <Titles board={first} />}
          <BoardRows
            board={first}
            rows={rows}
            hasMore={Boolean(board.hasNextPage)}
            loadingMore={board.isFetchingNextPage}
            onMore={() => void board.fetchNextPage()}
          />
          {board.isError && <ErrorAlert>{errorMessage(board.error)}</ErrorAlert>}
          <p className="text-sm text-slate-600">
            {RANKING_RULE} Only deeds a witness verified count; likes, comments and followers never
            do. “=2” means a shared place.
          </p>
        </>
      ) : null}
      <HeroesHistory regionCode={regionCode} />
    </FeedMain>
  )
}

/**
 * An open level with no place chosen: go to my own board of that level (the month asked for,
 * else the newest), or ask me to find a place.
 */
function LevelLanding({ level, month }: { level: RegionKind; month: string | null }) {
  const mine = useMyScoring()
  if (mine.isPending) return <Loading label="Loading your leaderboard…" />
  const boards = (mine.data?.boards ?? []).filter((b) => b.region.kind === level)
  const target = boards.find((b) => b.period_month === month) ?? boards[0]
  if (target) {
    const suffix = month ? `?month=${month}` : ''
    return (
      <Navigate replace to={`/leaderboard/${encodeURIComponent(target.region.code)}${suffix}`} />
    )
  }
  const noun = LEVEL_NOUN[level]
  return (
    <>
      <header className="flex flex-col gap-1">
        <h1 className="text-[26px] leading-tight font-bold tracking-tight text-slate-900">
          {LEVEL_LABEL[level]} leaderboard
        </h1>
        <p className="text-slate-600">
          Find your {noun.one} to see who is leading this month. Once a witness verifies one of your
          deeds, your {noun.one}’s board opens here for you.
        </p>
      </header>
      <RegionPicker kind={level} month={month} />
      <FeaturedCity month={month} />
    </>
  )
}

/** A level the config has not opened yet (e.g. `?level=city`). */
function LockedLevel({ level, month }: { level: RegionKind; month: string | null }) {
  return (
    <section
      aria-labelledby="locked-level-h"
      className="flex flex-col items-start gap-3 rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6"
    >
      <span className="inline-flex size-10 items-center justify-center rounded-full bg-slate-200 text-slate-600">
        <LockIcon className="size-5" />
      </span>
      <h1 id="locked-level-h" className="text-[22px] leading-tight font-bold text-slate-900">
        {LEVEL_LABEL[level]} leaderboards are locked
      </h1>
      <p className="text-slate-600">
        They’re coming soon. For now, see who is leading in your area.
      </p>
      <Link to={levelHref(DEFAULT_LEVEL, month)} className={secondaryButton}>
        {LEVEL_LABEL[DEFAULT_LEVEL]} leaderboard
      </Link>
    </section>
  )
}

/** Shortcuts to my own boards of this level (e.g. the areas I'm ranked in). */
function MyBoards({
  level,
  regionCode,
  month,
}: {
  level: RegionKind
  regionCode: string
  month: string | null
}) {
  const mine = useMyScoring()
  const suffix = month ? `?month=${month}` : ''
  const boards = (mine.data?.boards ?? [])
    .filter((b) => b.region.kind === level)
    .filter((b, i, all) => all.findIndex((o) => o.region.code === b.region.code) === i)
  if (boards.length === 0) return null
  const chip = (active: boolean) =>
    `inline-flex min-h-11 items-center rounded-full px-4 text-sm font-semibold ${focusRing} ${
      active ? 'bg-brand-100 text-brand-900' : 'border border-slate-200 bg-white text-slate-800'
    }`
  return (
    <nav aria-label="Your leaderboards">
      <ul className="flex flex-wrap gap-2">
        {boards.map((b) => (
          <li key={b.region.code}>
            <Link
              to={`/leaderboard/${encodeURIComponent(b.region.code)}${suffix}`}
              aria-current={regionCode === b.region.code ? 'page' : undefined}
              className={chip(regionCode === b.region.code)}
            >
              {b.region.name}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  )
}

function MonthPicker({
  months,
  value,
  onChange,
}: {
  months: string[]
  value: string
  onChange: (month: string) => void
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-semibold text-slate-700">
        Month
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`${inputClass} border-slate-300`}
      >
        {months.map((m) => (
          <option key={m} value={m}>
            {formatMonth(m)}
          </option>
        ))}
      </select>
    </div>
  )
}

/** "Final", or "Provisional until 2 Dec, 01:00 IST" (the date only when the API sends it). */
function StatusBanner({ board }: { board: Leaderboard }) {
  if (board.final) {
    return (
      <p
        role="status"
        className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3 text-[15px] text-emerald-900"
      >
        <strong>Final.</strong> This month’s ranks and titles are settled.
      </p>
    )
  }
  const until = board.finalizes_at ? formatWithdrawUntil(board.finalizes_at) : ''
  return (
    <p
      role="status"
      className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-[15px] text-amber-900"
    >
      <strong>{until ? `Provisional until ${until} IST.` : 'Provisional.'}</strong> Ranks can still
      change until the month is final.
    </p>
  )
}

/** The Kindness Heroes of a final board ("#1 · Kindness Hero · Koramangala · November 2026"). */
function Titles({ board }: { board: Leaderboard }) {
  const options = useScoringOptions(board.titles.length === 0)
  if (board.titles.length === 0) {
    const level = options.data?.levels.find((l) => l.kind === board.region.kind)
    // Only levels with minimums can end a month without a Hero while people are on the board.
    const gated = board.region.kind === 'ward' || Boolean(level?.hero_min_deeds)
    return gated ? (
      <p className="rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3 text-[15px] text-slate-700">
        {noHeroText(level)}
      </p>
    ) : null
  }
  return (
    <section aria-labelledby="board-titles-h" className="flex flex-col gap-2">
      <h2 id="board-titles-h" className="text-lg font-bold text-slate-900">
        Kindness Heroes
      </h2>
      <TitleList titles={board.titles} />
    </section>
  )
}

function TitleList({ titles }: { titles: BoardTitle[] }) {
  return (
    <ul className="flex flex-col gap-2">
      {titles.map((title) => (
        <li
          key={`${title.place}-${title.label}`}
          className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5"
        >
          <p className="font-semibold text-amber-900">
            #{title.place} · {title.label}
          </p>
          <p className="text-[15px] text-slate-800">
            {title.users.map((u, i) => (
              <span key={u.handle ?? `unavailable-${i}`}>
                {i > 0 && ', '}
                {u.handle === null ? (
                  <span className="font-medium text-slate-600">{u.display_name}</span>
                ) : (
                  <Link
                    to={`/u/${encodeURIComponent(u.handle)}`}
                    className={`font-medium underline-offset-2 hover:underline ${focusRing}`}
                  >
                    {u.display_name}
                  </Link>
                )}
              </span>
            ))}
          </p>
        </li>
      ))}
    </ul>
  )
}

function BoardRows({
  board,
  rows,
  hasMore,
  loadingMore,
  onMore,
}: {
  board: Leaderboard
  rows: LeaderboardRow[]
  hasMore: boolean
  loadingMore: boolean
  onMore: () => void
}) {
  const { user } = useAuth()
  const myHandle = (board.me?.user.handle ?? user?.handle ?? '').toLowerCase()
  // Title holders on a final board get a marker on their row.
  const titleOf = new Map<string, string>()
  if (board.final) {
    for (const title of board.titles) {
      for (const u of title.users) {
        if (u.handle !== null && !titleOf.has(u.handle.toLowerCase())) {
          titleOf.set(u.handle.toLowerCase(), title.label)
        }
      }
    }
  }
  const row = (r: LeaderboardRow) => {
    const handle = r.user.handle?.toLowerCase() ?? null
    return (
      <Row
        row={r}
        me={handle !== null && handle === myHandle}
        title={handle === null ? null : (titleOf.get(handle) ?? null)}
      />
    )
  }

  if (rows.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-slate-600">
        No one is on this board yet. Deeds verified by a witness this month will show up here.
      </p>
    )
  }
  // Your row is highlighted where it is; it is pinned above the list only while it is not
  // among the rows shown (further down the board), with your real rank.
  const listed = rows.some((r) => r.user.handle?.toLowerCase() === myHandle)
  return (
    <div className="flex flex-col gap-3">
      {board.me && !listed && (
        <section
          aria-labelledby="your-place-h"
          className="flex flex-col gap-1 overflow-hidden rounded-2xl border border-brand-600/35 bg-white pt-2"
        >
          <h3
            id="your-place-h"
            className="px-3.5 text-xs font-bold tracking-wide text-brand-700 uppercase"
          >
            Your place
          </h3>
          {row(board.me)}
        </section>
      )}
      <ol
        aria-label="Ranking"
        className="flex flex-col divide-y divide-slate-200 overflow-hidden rounded-2xl border border-slate-200 bg-white"
      >
        {rows.map((r, i) => (
          <li key={r.user.handle ?? `unavailable-${r.rank}-${i}`}>{row(r)}</li>
        ))}
      </ol>
      {hasMore && (
        <button
          type="button"
          className={`${secondaryButton} self-center`}
          disabled={loadingMore}
          onClick={onMore}
        >
          {loadingMore ? 'Loading…' : 'Show more'}
        </button>
      )}
    </div>
  )
}

function Row({ row, me, title }: { row: LeaderboardRow; me: boolean; title: string | null }) {
  return (
    <div
      data-testid="board-row"
      data-me={me || undefined}
      className={`flex items-center gap-3 px-3.5 py-3 ${me ? 'bg-brand-50 ring-2 ring-brand-600/35 ring-inset' : ''}`}
    >
      <span
        aria-label={row.shared ? `Rank ${row.rank}, shared` : `Rank ${row.rank}`}
        className="w-9 shrink-0 text-center text-lg font-bold text-slate-900 tabular-nums"
      >
        {rankText(row)}
      </span>
      <Avatar name={row.user.display_name} handle={row.user.handle ?? ''} size="md" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {row.user.handle === null ? (
          <span className="truncate font-semibold text-slate-600">{row.user.display_name}</span>
        ) : (
          <Link
            to={`/u/${encodeURIComponent(row.user.handle)}`}
            className={`truncate font-semibold text-slate-900 underline-offset-2 hover:underline ${focusRing}`}
          >
            {row.user.display_name}
            {me && <span className="font-normal text-slate-600"> (you)</span>}
          </Link>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <TierChip tier={row.tier} />
          {title && (
            <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-bold text-amber-900">
              {title}
            </span>
          )}
        </div>
      </div>
      <span className="shrink-0 text-[15px] text-slate-700">{deedsText(row.deed_count)}</span>
    </div>
  )
}

function BoardError({ error, retry }: { error: unknown; retry: () => void }) {
  if (error instanceof ApiError && error.status === 404) {
    return (
      <div className="flex flex-col items-start gap-3">
        <h1 className="text-[26px] leading-tight font-bold text-slate-900">Board not found</h1>
        <p className="text-slate-600">
          We couldn’t find an open leaderboard for that place. Search for an area above, or open
          your area board.
        </p>
        <Link to="/leaderboard" className={secondaryButton}>
          Area leaderboard
        </Link>
      </div>
    )
  }
  return (
    <div className="flex flex-col items-start gap-3">
      <h1 className="text-[26px] leading-tight font-bold text-slate-900">Leaderboard</h1>
      <ErrorAlert>{errorMessage(error)}</ErrorAlert>
      <button type="button" className={secondaryButton} onClick={retry}>
        Try again
      </button>
    </div>
  )
}

/** Past Kindness Heroes of this region (§3.3 #8): final months only, newest first. */
function HeroesHistory({ regionCode }: { regionCode: string }) {
  const heroes = useHeroes(regionCode)
  if (!heroes.data) return null
  return (
    <section aria-labelledby="heroes-h" className="flex flex-col gap-3">
      <h2 id="heroes-h" className="text-lg font-bold text-slate-900">
        Past Kindness Heroes
      </h2>
      {heroes.data.items.length === 0 ? (
        <p className="text-slate-600">
          No Heroes yet. Heroes are named once a month’s board is final.
        </p>
      ) : (
        heroes.data.items.map((item) => (
          <div key={item.period_month} className="flex flex-col gap-2">
            <h3 className="font-semibold text-slate-800">{formatMonth(item.period_month)}</h3>
            <TitleList titles={item.titles} />
          </div>
        ))
      )}
    </section>
  )
}
