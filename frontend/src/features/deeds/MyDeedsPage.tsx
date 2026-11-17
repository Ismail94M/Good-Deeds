import { useInfiniteQuery } from '@tanstack/react-query'
import { useRef, useState, type KeyboardEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { DeedCard } from './DeedCard'
import { useDeedOptions } from './deedQueries'
import {
  DEFAULT_SAME_ACT_CAP,
  deedsApi,
  deedsKeys,
  type DeedOut,
  type MyDeedsRole,
} from './deedsApi'
import {
  BackLink,
  EmptyState,
  F6Main,
  F6Title,
  focusRing,
  HeartIcon,
  primaryButtonClass,
  secondaryButtonClass,
} from './deedUi'
import { WithdrawDeedDialog } from './WithdrawDeedDialog'

type Tab = 'recognised' | 'reported'

const TABS: { id: Tab; label: string; role: MyDeedsRole; intro: string }[] = [
  {
    id: 'recognised',
    label: 'Recognised for',
    role: 'doer',
    intro: 'Deeds other people saw you do.',
  },
  {
    id: 'reported',
    label: 'I reported',
    role: 'reporter',
    intro: 'Deeds you saw others do. You can edit or withdraw your reports.',
  },
]

/**
 * `/me/deeds` (F6 canvas): two tabs while invites are off, Recognised for (default) and
 * I reported (`?tab=reported`). Lists come from `GET /api/me/deeds`, newest first, with
 * "Show more" while there is a next page.
 */
export function MyDeedsPage() {
  const [params, setParams] = useSearchParams()
  const tab: Tab = params.get('tab') === 'reported' ? 'reported' : 'recognised'
  const current = TABS.find((t) => t.id === tab) ?? TABS[0]
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({
    recognised: null,
    reported: null,
  })

  const select = (next: Tab, focus = false) => {
    setParams(next === 'reported' ? { tab: 'reported' } : {}, { replace: true })
    if (focus) tabRefs.current[next]?.focus()
  }

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault()
      select(tab === 'recognised' ? 'reported' : 'recognised', true)
    }
  }

  return (
    <F6Main>
      <BackLink to="/profile" label="Profile" />
      <F6Title>My deeds</F6Title>
      <div
        role="tablist"
        aria-label="My deeds"
        onKeyDown={onKeyDown}
        className="grid grid-cols-2 gap-1 rounded-xl bg-[#e9edf3] p-1"
      >
        {TABS.map((t) => {
          const selected = t.id === tab
          return (
            <button
              key={t.id}
              ref={(el) => {
                tabRefs.current[t.id] = el
              }}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`panel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => select(t.id)}
              className={`min-h-11 rounded-[9px] text-[15px] font-semibold ${focusRing} ${
                selected ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600'
              }`}
            >
              {t.label}
            </button>
          )
        })}
      </div>
      <div
        role="tabpanel"
        id={`panel-${current.id}`}
        aria-labelledby={`tab-${current.id}`}
        className="flex flex-col gap-3"
      >
        <p className="text-sm text-slate-600">{current.intro}</p>
        <DeedList key={current.role} role={current.role} />
      </div>
    </F6Main>
  )
}

function DeedList({ role }: { role: MyDeedsRole }) {
  const options = useDeedOptions()
  const [withdrawing, setWithdrawing] = useState<DeedOut | null>(null)
  const list = useInfiniteQuery({
    queryKey: deedsKeys.myDeeds(role),
    queryFn: ({ pageParam }) => deedsApi.myDeeds({ role, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })

  if (list.isPending) return <Loading label="Loading your deeds…" />
  if (list.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <ErrorAlert>{errorMessage(list.error)}</ErrorAlert>
        <button type="button" onClick={() => void list.refetch()} className={secondaryButtonClass}>
          Try again
        </button>
      </div>
    )
  }

  const deeds = list.data.pages.flatMap((page) => page.items)
  if (deeds.length === 0) {
    return role === 'doer' ? (
      <EmptyState
        icon={<HeartIcon />}
        title="No one has recognised you yet"
        body="Next time you help someone, show your QR so a witness can record it."
        action={
          <Link to="/me/qr" className={primaryButtonClass}>
            Show my QR
          </Link>
        }
      />
    ) : (
      <EmptyState
        icon={<HeartIcon />}
        title="You haven’t recorded a deed yet"
        body="Saw someone do something good? Record it while you’re both there."
        action={
          <Link to="/report" className={primaryButtonClass}>
            Record a deed
          </Link>
        }
      />
    )
  }

  const cap = options.data?.same_act_cap ?? DEFAULT_SAME_ACT_CAP
  return (
    <>
      <ul className="flex flex-col gap-3">
        {deeds.map((deed) => (
          <li key={deed.id}>
            <DeedCard
              deed={deed}
              viewer={role}
              categories={options.data?.categories}
              sameActCap={cap}
              onWithdraw={setWithdrawing}
            />
          </li>
        ))}
      </ul>
      {list.hasNextPage && (
        <button
          type="button"
          onClick={() => void list.fetchNextPage()}
          disabled={list.isFetchingNextPage}
          className={`${secondaryButtonClass} w-full`}
        >
          {list.isFetchingNextPage ? 'Loading…' : 'Show more'}
        </button>
      )}
      {list.isFetchNextPageError && (
        <ErrorAlert>
          We couldn’t load more deeds. Check your connection and tap Show more.
        </ErrorAlert>
      )}
      {withdrawing && (
        <WithdrawDeedDialog deed={withdrawing} onClose={() => setWithdrawing(null)} />
      )}
    </>
  )
}
