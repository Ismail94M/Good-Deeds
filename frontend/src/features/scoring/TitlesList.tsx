import type { Badge, HeroTitle } from './scoringApi'

/** A person's badges by their API labels ("First recognition", "5 deeds"). */
export function BadgeList({ badges }: { badges: Badge[] }) {
  if (badges.length === 0) return <p className="text-sm text-slate-600">No badges yet.</p>
  return (
    <ul className="flex flex-wrap gap-2">
      {badges.map((badge) => (
        <li
          key={badge.slug}
          className="rounded-full bg-amber-50 px-3 py-1 text-sm font-medium text-amber-900 ring-1 ring-amber-200"
        >
          {badge.label}
        </li>
      ))}
    </ul>
  )
}

/** Kindness Hero titles, worded by the server ("Kindness Hero · Koramangala · November 2026"). */
export function TitlesList({ titles }: { titles: HeroTitle[] }) {
  if (titles.length === 0) return <p className="text-sm text-slate-600">No Hero titles yet.</p>
  return (
    <ul className="flex flex-col gap-1.5">
      {titles.map((title) => (
        <li
          key={`${title.period_month}-${title.region_name}-${title.place}-${title.label}`}
          className="text-[15px] text-slate-800"
        >
          {title.label}
        </li>
      ))}
    </ul>
  )
}
