import type { ReactNode } from 'react'
import type { RegionKind, UserRank, UserRanks } from './scoringApi'
import { useUserRanks } from './scoringQueries'

/** The full board order (D59 rank score, D67 tie-breaks), said the same everywhere. */
export const RANKING_RULE =
  'Ranked by verified deeds this month, then points, then the number of different witnesses, then the number of different days.'

/** "#24"; a shared place reads "=3", as on the boards. */
export function rankText(rank: UserRank): string {
  return rank.rank === null ? '–' : rank.shared ? `=${rank.rank}` : `#${rank.rank}`
}

function level(ranks: UserRanks | undefined, kind: RegionKind): UserRank | undefined {
  return ranks?.levels.find((l) => l.level === kind)
}

/**
 * The City Rank figure for a profile's stats (D76): "#24", "=3", "–" when not ranked this
 * month, "…" while loading. `unranked` marks the "–" of someone on no City board this month.
 */
export function useCityRank(handle: string, enabled: boolean) {
  const ranks = useUserRanks(handle, enabled)
  const city = level(ranks.data, 'city')
  const value: ReactNode = ranks.isError ? '–' : !city ? '…' : rankText(city)
  const unranked = city !== undefined && city.rank === null
  return { ranks, city, value, unranked }
}
