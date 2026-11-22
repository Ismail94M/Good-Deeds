/**
 * TanStack Query hooks for the M4 scoring API (M4-DESIGN §3.3, §6.4). Reads only: scoring has
 * no member writes. Points hooks (`useMyScoring`, `useLedger`, `useDeedPoints`) are for the
 * owner's own screens; public screens use `useUserScoring` and the leaderboard hooks.
 */

import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { retryUnlessClientError } from '../feed/feedQueries'
import { scoringApi, scoringKeys, type LedgerRole, type RegionKind } from './scoringApi'

const firstPage = null as string | null
const nextCursor = (page: { next_cursor: string | null }) => page.next_cursor

/** `GET /scoring/options` (#1): tiers, badges, levels, window hours and caps for copy. */
export function useScoringOptions(enabled = true) {
  return useQuery({
    queryKey: scoringKeys.options,
    queryFn: scoringApi.options,
    staleTime: 5 * 60_000,
    retry: retryUnlessClientError,
    enabled,
  })
}

/** `GET /scoring/me` (#2): my points, provisional points, boards and reporter cap. */
export function useMyScoring(enabled = true) {
  return useQuery({
    queryKey: scoringKeys.me,
    queryFn: scoringApi.me,
    retry: retryUnlessClientError,
    enabled,
  })
}

/** `GET /scoring/me/ledger` (#3): my points history, newest first, page by page. */
export function useLedger(role: LedgerRole | null = null) {
  return useInfiniteQuery({
    queryKey: scoringKeys.ledger(role),
    queryFn: ({ pageParam }) => scoringApi.ledger({ role, cursor: pageParam }),
    initialPageParam: firstPage,
    getNextPageParam: nextCursor,
  })
}

/** `GET /scoring/users/{handle}` (#4): tier, badges and titles; no points. Needs sign-in. */
export function useUserScoring(handle: string, enabled = true) {
  return useQuery({
    queryKey: scoringKeys.user(handle),
    queryFn: () => scoringApi.user(handle),
    retry: retryUnlessClientError,
    enabled,
  })
}

/** `GET /scoring/deeds/{id}` (#5): my own points line on a deed (404 for anyone else). */
export function useDeedPoints(deedId: string, enabled = true) {
  return useQuery({
    queryKey: scoringKeys.deed(deedId),
    queryFn: () => scoringApi.deed(deedId),
    retry: retryUnlessClientError,
    enabled,
  })
}

/**
 * `GET /leaderboards` (#6) for one region and month (null = city board / current IST month).
 * Keeps the previous board on screen while another month or region loads.
 */
export function useLeaderboard(region: string | null = null, month: string | null = null) {
  return useInfiniteQuery({
    queryKey: scoringKeys.leaderboard(region, month),
    queryFn: ({ pageParam }) => scoringApi.leaderboard({ region, month, cursor: pageParam }),
    initialPageParam: firstPage,
    getNextPageParam: nextCursor,
    retry: retryUnlessClientError,
    placeholderData: keepPreviousData,
  })
}

/** `GET /leaderboards/regions` (#7): ward picker search. Runs once `q` has 2+ characters. */
export function useRegionSearch(q: string, kind: RegionKind = 'ward') {
  const term = q.trim()
  return useQuery({
    queryKey: scoringKeys.regions(kind, term),
    queryFn: () => scoringApi.regions({ kind, q: term }),
    enabled: term.length >= 2,
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  })
}

/** `GET /leaderboards/heroes` (#8): final titles per month for a region and year. */
export function useHeroes(region: string | null = null, year: number | null = null) {
  return useQuery({
    queryKey: scoringKeys.heroes(region, year),
    queryFn: () => scoringApi.heroes({ region, year }),
    retry: retryUnlessClientError,
  })
}

/** `GET /admin/scoring/deeds/{id}` (#9, admin only). */
export function useAdminDeedScoring(deedId: string) {
  return useQuery({
    queryKey: scoringKeys.adminDeed(deedId),
    queryFn: () => scoringApi.adminDeed(deedId),
    retry: retryUnlessClientError,
  })
}

/** `GET /admin/scoring/months/{YYYY-MM}` (#10, admin only). */
export function useMonthFinalization(month: string) {
  return useQuery({
    queryKey: scoringKeys.adminMonth(month),
    queryFn: () => scoringApi.adminMonth(month),
    retry: retryUnlessClientError,
  })
}
