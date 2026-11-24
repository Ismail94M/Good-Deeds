import { useId, useState } from 'react'
import type { HeroTitle, RegionKind } from './scoringApi'
import { formatMonth } from './scoringText'

/** How many title cards show before the rest fold into "Show all N titles". */
const CARDS = 3

const LEVEL_NAME: Record<RegionKind, string> = {
  ward: 'Area',
  city: 'City',
  state: 'State',
  country: 'Country',
}
const LEVEL_RANK: Record<RegionKind, number> = { ward: 0, city: 1, state: 2, country: 3 }

type Look = 'crown' | 'podium' | 'area'

/**
 * Kindness Hero titles as achievement cards (PO 2026-11-24, D75). A #1 above Area level is the
 * "crown" card (deep indigo, gold crown); other places above Area are "podium" cards (indigo
 * outline, place medal); Area titles are gold "area" cards with a trophy. Most prestigious first:
 * higher level first, then better place, then newest. The first three show as cards; the rest
 * fold into a compact list.
 */
export function HeroTitles({ titles }: { titles: HeroTitle[] }) {
  const [all, setAll] = useState(false)
  const listId = useId()
  if (titles.length === 0) return null
  const sorted = [...titles].sort(
    (a, b) =>
      LEVEL_RANK[b.level] - LEVEL_RANK[a.level] ||
      a.place - b.place ||
      b.period_month.localeCompare(a.period_month),
  )
  const cards = sorted.slice(0, CARDS)
  const rest = sorted.slice(CARDS)
  return (
    <div className="flex flex-col gap-2.5">
      <ul className="grid gap-2.5 sm:grid-cols-2">
        {cards.map((title, i) => (
          <li key={key(title)} className={i === 0 ? 'sm:col-span-2' : ''}>
            <TitleCard title={title} />
          </li>
        ))}
      </ul>
      {rest.length > 0 && (
        <>
          {all && (
            <ul
              id={listId}
              className="grid rounded-2xl border border-slate-200 bg-white p-1 sm:grid-cols-2"
            >
              {rest.map((title) => (
                <li key={key(title)} className="flex items-center gap-3 px-3 py-2.5">
                  <span
                    aria-hidden
                    className={`flex size-8 shrink-0 items-center justify-center rounded-lg ${
                      title.level === 'ward'
                        ? 'bg-amber-100 text-amber-700'
                        : 'bg-indigo-50 text-indigo-700'
                    }`}
                  >
                    {title.level === 'ward' ? (
                      <TrophyIcon className="size-4" />
                    ) : (
                      <CrownIcon className="size-4" />
                    )}
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <span className="text-[14.5px] font-semibold text-slate-900">
                      {kicker(title)} · {headline(title)}
                    </span>
                    <span className="text-[12.5px] text-slate-600">
                      {formatMonth(title.period_month)}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            aria-expanded={all}
            aria-controls={listId}
            onClick={() => setAll((v) => !v)}
            className="min-h-11 self-center rounded px-2 text-sm font-semibold text-brand-700 hover:text-brand-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700"
          >
            {all ? 'Show fewer titles' : `Show all ${titles.length} titles`}
          </button>
        </>
      )}
    </div>
  )
}

const key = (t: HeroTitle) => `${t.level}-${t.period_month}-${t.region_name}-${t.place}-${t.label}`

function look(title: HeroTitle): Look {
  if (title.level === 'ward') return 'area'
  return title.place === 1 ? 'crown' : 'podium'
}

/** "City Kindness Hero · #1", "Area Kindness Hero · #1", "City · #2". */
function kicker(title: HeroTitle): string {
  const level = LEVEL_NAME[title.level]
  return title.place === 1 ? `${level} Kindness Hero · #1` : `${level} · #${title.place}`
}

/**
 * The card's big line: the place name for Area titles ("Koramangala"), otherwise the server's
 * wording ("Bangalore City Kindness Hero", "Top 3 in Bangalore") without a trailing month, which
 * the card shows on its own line.
 */
function headline(title: HeroTitle): string {
  if (title.level === 'ward' && title.region_name) return title.region_name
  const month = ` · ${formatMonth(title.period_month)}`
  return title.label.endsWith(month) ? title.label.slice(0, -month.length) : title.label
}

function TitleCard({ title }: { title: HeroTitle }) {
  const kind = look(title)
  const month = formatMonth(title.period_month)
  if (kind === 'crown') {
    return (
      <article
        data-look="crown"
        className="relative flex items-center gap-4 overflow-hidden rounded-[20px] bg-gradient-to-br from-indigo-900 to-violet-900 p-4 text-white shadow-[0_14px_28px_-16px_rgba(49,46,129,0.9)] sm:p-5"
      >
        <Laurel />
        <span
          aria-hidden
          className="flex size-14 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-amber-200 to-amber-600 text-amber-900 shadow-[0_0_0_3px_rgba(253,230,138,0.25),inset_0_2px_3px_rgba(255,255,255,0.5)]"
        >
          <CrownIcon className="size-7" filled />
        </span>
        <div className="relative flex min-w-0 flex-col gap-0.5">
          <span className="text-[11.5px] font-extrabold tracking-[0.08em] text-amber-300 uppercase">
            {kicker(title)}
          </span>
          <span className="text-[19px] leading-tight font-extrabold break-words">
            {headline(title)}
          </span>
          <span className="text-[13.5px] text-indigo-100">{month}</span>
        </div>
      </article>
    )
  }
  const area = kind === 'area'
  return (
    <article
      data-look={kind}
      className={`flex h-full items-center gap-3.5 rounded-[18px] px-4 py-3.5 ${
        area
          ? 'bg-gradient-to-br from-amber-50 to-white to-70% shadow-[inset_0_0_0_1px_#fcd34d,0_6px_16px_-12px_rgba(180,83,9,0.6)]'
          : 'bg-white shadow-[inset_0_0_0_1px_#c7d2fe,0_6px_16px_-12px_rgba(67,56,202,0.6)]'
      }`}
    >
      {area ? (
        <span
          aria-hidden
          className="flex size-12 shrink-0 items-center justify-center rounded-[14px] bg-gradient-to-br from-amber-200 to-amber-500 text-amber-900 shadow-[inset_0_2px_3px_rgba(255,255,255,0.55)]"
        >
          <TrophyIcon className="size-6" />
        </span>
      ) : (
        <span
          aria-hidden
          className={`flex size-12 shrink-0 items-center justify-center rounded-full text-lg font-extrabold text-slate-800 shadow-[inset_0_2px_3px_rgba(255,255,255,0.7)] ${
            title.place === 2
              ? 'bg-gradient-to-br from-slate-50 to-slate-400'
              : 'bg-gradient-to-br from-orange-100 to-orange-400'
          }`}
        >
          {title.place}
        </span>
      )}
      <div className="flex min-w-0 flex-col gap-0.5">
        <span
          className={`text-[11.5px] font-extrabold tracking-[0.08em] uppercase ${
            area ? 'text-amber-800' : 'text-indigo-700'
          }`}
        >
          {kicker(title)}
        </span>
        <span className="text-[16.5px] leading-tight font-extrabold break-words text-slate-900">
          {headline(title)}
        </span>
        <span className="text-[13px] text-stone-600">{month}</span>
      </div>
    </article>
  )
}

function Laurel() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 120 120"
      className="pointer-events-none absolute -right-7 -bottom-9 size-40 opacity-15"
      fill="none"
      stroke="#fcd34d"
      strokeWidth="3"
      strokeLinecap="round"
    >
      <path d="M60 108c-26-6-42-26-42-52M60 108c26-6 42-26 42-52" />
      <path d="M22 70c6 1 10-2 12-7M18 56c6-1 9-5 9-11M22 41c5-2 7-7 6-12M86 63c2 5 6 8 12 7M93 45c0 6 3 10 9 11M92 29c-1 5 1 10 6 12" />
    </svg>
  )
}

export function CrownIcon({ className, filled = false }: { className: string; filled?: boolean }) {
  return filled ? (
    <svg aria-hidden viewBox="0 0 24 24" className={className} fill="currentColor">
      <path d="M3.5 8.2l4.6 3.6L12 5l3.9 6.8 4.6-3.6-1.9 10.3H5.4z" />
      <rect x="5.4" y="19.6" width="13.2" height="1.9" rx=".9" />
    </svg>
  ) : (
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
      <path d="M4 8l4 3.5L12 5l4 6.5L20 8l-1.6 10H5.6z" />
      <path d="M5.6 21h12.8" />
    </svg>
  )
}

function TrophyIcon({ className }: { className: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M7.5 4h9v5a4.5 4.5 0 0 1-9 0z" />
      <path d="M7.5 6H4.5c0 3 1.4 4.6 3.4 4.9M16.5 6h3c0 3-1.4 4.6-3.4 4.9M12 13.5V17M8.5 20.5h7M9.5 17h5v3.5h-5z" />
    </svg>
  )
}
