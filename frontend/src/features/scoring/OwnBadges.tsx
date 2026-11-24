import { useEffect, useState } from 'react'
import { BadgeShelf } from './BadgeShelf'
import { nextBadges, readSeenBadges, writeSeenBadges } from './badges'
import type { Badge } from './scoringApi'
import { useMyScoring, useScoringOptions } from './scoringQueries'

/**
 * "Your badges" on my own profile (PO 2026-11-24): my medals, plus the next badge on each ladder
 * as a locked medal. A badge this device hasn't shown me before plays the unlock animation once.
 * Hidden while loading or on error, like the points line above it.
 */
export function OwnBadges({ handle }: { handle: string }) {
  const scoring = useMyScoring()
  const options = useScoringOptions()
  if (!scoring.data) return null
  const offered = options.data?.badges
  return <Shelf handle={handle} badges={scoring.data.badges} offered={offered} />
}

function Shelf({
  handle,
  badges,
  offered,
}: {
  handle: string
  badges: Badge[]
  offered?: { slug: string; label: string; description: string }[]
}) {
  // Decided once per visit, so the animation isn't cut short when the seen list is saved.
  const [fresh] = useState(() => {
    const seen = new Set(readSeenBadges(handle) ?? [])
    return new Set(badges.map((b) => b.slug).filter((slug) => !seen.has(slug)))
  })
  const slugs = badges.map((b) => b.slug).join(',')
  useEffect(() => {
    const seen = readSeenBadges(handle) ?? []
    writeSeenBadges(handle, [...seen, ...(slugs ? slugs.split(',') : [])])
  }, [handle, slugs])

  const descriptions = offered && Object.fromEntries(offered.map((b) => [b.slug, b.description]))
  const locked = offered ? nextBadges(badges, offered) : []
  const count = badges.length
  return (
    <section
      aria-labelledby="own-badges-h"
      className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white px-4 pt-3.5 pb-4"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="own-badges-h" className="font-semibold text-slate-900">
          Your badges{count > 0 && <span className="font-normal text-slate-500"> · {count}</span>}
        </h2>
        <p className="text-xs text-slate-500">Tap a badge for details</p>
      </div>
      <BadgeShelf
        badges={badges}
        descriptions={descriptions}
        locked={locked}
        fresh={fresh}
        emptyText="No badges yet. Your first one comes with your first verified deed."
      />
    </section>
  )
}
