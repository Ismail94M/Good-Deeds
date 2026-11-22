import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/**
 * My own "{n} Self-log Deeds | {n} Verified Deeds" on `/profile` (D4, D15), linking to
 * self-logs and My deeds. Since M4 (D69, PO Q55) the self-log count is private: it comes from
 * `total` on my own `GET /me/self-logs` (sharing the self-logs list's cache), and
 * `GET /users/{handle}/deed-counts` returns only `verified`. If the API doesn't send `total`,
 * the Self-logs tile shows no number.
 */
export function DeedCounts({ handle }: { handle: string }) {
  const counts = useQuery({
    queryKey: deedsKeys.counts(handle),
    queryFn: () => deedsApi.deedCounts(handle),
    retry: false,
  })
  const selfLogs = useInfiniteQuery({
    queryKey: deedsKeys.selfLogs,
    queryFn: ({ pageParam }) => deedsApi.selfLogs(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    retry: false,
  })
  const value = (failed: boolean, n: number | undefined) => (failed ? '–' : (n ?? '…'))
  const selfLogTotal = selfLogs.data?.pages[0]?.total
  // Loaded but without `total`: hide the number rather than guess it from one page.
  const hideSelfLogCount = selfLogs.isSuccess && typeof selfLogTotal !== 'number'

  return (
    <div
      role="group"
      aria-label="Deed counts"
      aria-busy={counts.isPending || selfLogs.isPending || undefined}
      className="grid grid-cols-2 overflow-hidden rounded-2xl border border-slate-200 bg-white"
    >
      <Count
        n={hideSelfLogCount ? null : value(selfLogs.isError, selfLogTotal)}
        label={hideSelfLogCount ? 'Self-logs' : 'Self-log Deeds'}
        to="/me/self-logs"
      />
      <Count
        n={value(counts.isError, counts.data?.verified)}
        label="Verified Deeds"
        to="/me/deeds"
        divider
      />
    </div>
  )
}

function Count({
  n,
  label,
  to,
  divider = false,
}: {
  n: ReactNode
  label: string
  to: string
  divider?: boolean
}) {
  return (
    <Link
      to={to}
      className={`flex flex-col items-center justify-center gap-0.5 px-2 py-4 text-slate-900 hover:bg-brand-50/50 ${focusRing} ${
        divider ? 'border-l border-slate-200' : ''
      }`}
    >
      {n !== null && (
        <>
          <span className="text-[26px] leading-tight font-bold">{n}</span>{' '}
        </>
      )}
      <span className="text-sm font-medium text-brand-700">{label}</span>
    </Link>
  )
}
