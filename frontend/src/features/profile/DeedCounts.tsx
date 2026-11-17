import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/**
 * "{n} Self-log Deeds | {n} Verified Deeds" from `GET /users/{handle}/deed-counts` (D4, D15).
 * On your own profile both link through (self-logs, My deeds); on a public profile they are
 * plain numbers.
 */
export function DeedCounts({ handle, own }: { handle: string; own: boolean }) {
  const counts = useQuery({
    queryKey: deedsKeys.counts(handle),
    queryFn: () => deedsApi.deedCounts(handle),
    retry: false,
  })
  const value = (n: number | undefined) => (counts.isError ? '–' : (n ?? '…'))

  return (
    <div
      role="group"
      aria-label="Deed counts"
      aria-busy={counts.isPending || undefined}
      className="grid grid-cols-2 overflow-hidden rounded-2xl border border-slate-200 bg-white"
    >
      <Count
        n={value(counts.data?.self_logged)}
        label="Self-log Deeds"
        to={own ? '/me/self-logs' : null}
      />
      <Count
        n={value(counts.data?.verified)}
        label="Verified Deeds"
        to={own ? '/me/deeds' : null}
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
  to: string | null
  divider?: boolean
}) {
  const className = `flex flex-col items-center gap-0.5 px-2 py-4 text-slate-900 ${
    divider ? 'border-l border-slate-200' : ''
  }`
  const content = (
    <>
      <span className="text-[26px] leading-tight font-bold">{n}</span>{' '}
      <span className={`text-sm font-medium ${to ? 'text-brand-700' : 'text-slate-600'}`}>
        {label}
      </span>
    </>
  )
  return to ? (
    <Link to={to} className={`${className} hover:bg-brand-50/50 ${focusRing}`}>
      {content}
    </Link>
  ) : (
    <div className={className}>{content}</div>
  )
}
