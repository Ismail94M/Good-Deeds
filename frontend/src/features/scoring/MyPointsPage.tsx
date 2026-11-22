import { useId, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { formatWithdrawUntil } from '../deeds/withdrawWindow'
import { FeedMain, focusRing, SegmentedTabs, secondaryButton } from '../feed/feedUi'
import { firstName, formatShortDate } from '../report/format'
import type { LedgerEntry, LedgerRole, MyBoard, MyScoring } from './scoringApi'
import { useLedger, useMyScoring, useScoringOptions } from './scoringQueries'
import { deedsText, formatMonth, signedPoints } from './scoringText'
import { TierChip } from './TierChip'

type RoleTab = 'all' | LedgerRole

const ROLE_TABS: { id: RoleTab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'doer', label: 'My deeds' },
  { id: 'reporter', label: 'My reports' },
]

/**
 * `/me/points` (M4-DESIGN §6.3, F16): my total and provisional points, the doer / reporter split,
 * today's reporter cap, my board places for the month, and the points history page by page.
 * Owner only: nothing here is shown to anyone else (D69). All numbers come from the server
 * (`total_points` included, §3.4 item 5); history rows show category and names, never the
 * description.
 */
export function MyPointsPage() {
  const scoring = useMyScoring()
  const options = useScoringOptions()
  const hours = options.data?.withdraw_window_hours

  return (
    <FeedMain>
      <h1 className="text-[26px] leading-tight font-bold tracking-tight text-slate-900">
        My points
      </h1>
      {scoring.isPending ? (
        <Loading label="Loading your points…" />
      ) : scoring.isError ? (
        <div className="flex flex-col items-start gap-3">
          <ErrorAlert>{errorMessage(scoring.error)}</ErrorAlert>
          <button type="button" className={secondaryButton} onClick={() => void scoring.refetch()}>
            Try again
          </button>
        </div>
      ) : (
        <>
          <Summary scoring={scoring.data} />
          <Boards boards={scoring.data.boards} />
        </>
      )}
      <p className="text-sm text-slate-600">
        {hours
          ? `Points become final ${hours} hours after a deed is verified.`
          : 'Points become final a short time after a deed is verified.'}{' '}
        Only you can see your points. Others see your tier, badges and Hero titles.
      </p>
      <History />
    </FeedMain>
  )
}

function Summary({ scoring }: { scoring: MyScoring }) {
  const { total_points, provisional_points, doer_points, reporter_points, reporter_today } = scoring
  return (
    <section
      aria-label="Your points"
      className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white px-4.5 py-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-slate-700">
          <span className="text-[32px] leading-tight font-bold text-slate-900">{total_points}</span>{' '}
          points
          {provisional_points > 0 && (
            <span className="block text-sm text-slate-600">
              {provisional_points} still provisional
            </span>
          )}
        </p>
        <TierChip tier={scoring.tier} />
      </div>
      <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 text-[15px]">
        <dt className="text-slate-600">From deeds you did</dt>
        <dd className="font-semibold">{doer_points}</dd>
        <dt className="text-slate-600">From deeds you reported</dt>
        <dd className="font-semibold">{reporter_points}</dd>
        <dt className="text-slate-600">Reports earning points today</dt>
        <dd className="font-semibold">
          {reporter_today.used} of {reporter_today.cap}
        </dd>
      </dl>
    </section>
  )
}

/** "#4 in Koramangala · provisional" cards, each opening its board for that month. */
function Boards({ boards }: { boards: MyBoard[] }) {
  return (
    <section aria-labelledby="my-boards-h" className="flex flex-col gap-2.5">
      <h2 id="my-boards-h" className="text-lg font-bold text-slate-900">
        Your leaderboards
      </h2>
      {boards.length === 0 ? (
        <p className="text-slate-600">
          You’re not on a leaderboard yet. Deeds that witnesses verify put you on the board for your
          ward and for the city.{' '}
          <Link to="/leaderboard" className={`font-semibold text-brand-700 underline ${focusRing}`}>
            See the leaderboard
          </Link>
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {boards.map((board) => (
            <li key={`${board.region.id}-${board.period_month}`}>
              <Link
                to={`/leaderboard/${encodeURIComponent(board.region.code)}?month=${board.period_month}`}
                className={`flex flex-col rounded-2xl border border-slate-200 bg-white px-4 py-3 hover:bg-slate-50 ${focusRing}`}
              >
                <span className="font-semibold text-slate-900">
                  {board.rank === null
                    ? `Not ranked yet in ${board.region.name}`
                    : `#${board.rank} in ${board.region.name}`}
                </span>
                <span className="text-sm text-slate-600">
                  {formatMonth(board.period_month)} · {deedsText(board.deed_count)} ·{' '}
                  {board.final ? 'Final' : 'Provisional'}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function History() {
  const [tab, setTab] = useState<RoleTab>('all')
  const panelId = useId()
  const ledger = useLedger(tab === 'all' ? null : tab)
  const data = ledger.data
  const entries = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])

  return (
    <section aria-labelledby="points-history-h" className="flex flex-col gap-3">
      <h2 id="points-history-h" className="text-lg font-bold text-slate-900">
        History
      </h2>
      <SegmentedTabs
        label="Show points from"
        tabs={ROLE_TABS}
        value={tab}
        onChange={setTab}
        panelId={panelId}
      />
      <div id={panelId} role="tabpanel" className="flex flex-col gap-3">
        {ledger.isPending ? (
          <Loading label="Loading your history…" />
        ) : ledger.isError && entries.length === 0 ? (
          <div className="flex flex-col items-start gap-3">
            <ErrorAlert>{errorMessage(ledger.error)}</ErrorAlert>
            <button type="button" className={secondaryButton} onClick={() => void ledger.refetch()}>
              Try again
            </button>
          </div>
        ) : entries.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-slate-600">
            No points yet. When a witness verifies a deed, its points show up here.
          </p>
        ) : (
          <>
            <ul className="flex flex-col divide-y divide-slate-200 rounded-2xl border border-slate-200 bg-white">
              {entries.map((entry) => (
                <LedgerRow key={entry.id} entry={entry} />
              ))}
            </ul>
            {ledger.hasNextPage && (
              <button
                type="button"
                className={`${secondaryButton} self-center`}
                disabled={ledger.isFetchingNextPage}
                onClick={() => void ledger.fetchNextPage()}
              >
                {ledger.isFetchingNextPage ? 'Loading…' : 'Show more'}
              </button>
            )}
            {ledger.isError && <ErrorAlert>{errorMessage(ledger.error)}</ErrorAlert>}
          </>
        )}
      </div>
    </section>
  )
}

/** "+10 · Deed verified" over "Medical help · recognised by Priya · provisional until …". */
function LedgerRow({ entry }: { entry: LedgerEntry }) {
  const details: string[] = []
  if (entry.category_label) details.push(entry.category_label)
  if (entry.role === 'doer' && entry.reporter) {
    details.push(`recognised by ${firstName(entry.reporter.display_name)}`)
  } else if (entry.role === 'reporter' && entry.doer) {
    details.push(`You recognised ${firstName(entry.doer.display_name)}`)
  }
  if (entry.provisional && entry.provisional_until) {
    details.push(`provisional until ${formatWithdrawUntil(entry.provisional_until)}`)
  }
  const tone =
    entry.points > 0 ? 'text-emerald-700' : entry.points < 0 ? 'text-red-700' : 'text-slate-600'
  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <span className={`w-12 shrink-0 text-lg font-bold tabular-nums ${tone}`}>
        {signedPoints(entry.points)}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="font-semibold text-slate-900">{entry.reason_label}</p>
        {details.length > 0 && <p className="text-sm text-slate-600">{details.join(' · ')}</p>}
        <p className="text-sm text-slate-500">
          {formatShortDate(entry.created_at)} ·{' '}
          <Link
            to={`/me/deeds/${encodeURIComponent(entry.deed_id)}`}
            className={`font-semibold text-brand-700 underline-offset-2 hover:underline ${focusRing}`}
          >
            View deed
          </Link>
        </p>
      </div>
    </li>
  )
}
