import { useId, useRef, type ChangeEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { inputClass } from '../../components/ui'
import {
  DEFAULT_FEED_TAB,
  DEFAULT_TOP_PERIOD,
  FEED_TABS,
  TOP_PERIODS,
  type FeedTab,
  type TopPeriod,
} from './feedApi'
import { useFeed, useFeedOptions } from './feedQueries'
import { periodLabel, rankChip, TAB_LABELS, tabIntro } from './feedText'
import {
  CircleCheckIcon,
  EmptyBox,
  FeedMain,
  PeopleIcon,
  primaryButton,
  SegmentedTabs,
  secondaryButton,
} from './feedUi'
import { FeedNotices } from './FeedNotices'
import { PostList } from './PostList'
import { StoriesBar } from './StoriesBar'

function parseTab(value: string | null): FeedTab {
  return FEED_TABS.includes(value as FeedTab) ? (value as FeedTab) : DEFAULT_FEED_TAB
}

function isPeriod(value: string | null): value is TopPeriod {
  return TOP_PERIODS.includes(value as TopPeriod)
}

/** The last Top period picked in this tab's session, so Top → Nearby → Top keeps it. */
const PERIOD_KEY = 'gd.feed.topPeriod'

function rememberedPeriod(): TopPeriod {
  try {
    const value = window.sessionStorage.getItem(PERIOD_KEY)
    return isPeriod(value) ? value : DEFAULT_TOP_PERIOD
  } catch {
    return DEFAULT_TOP_PERIOD
  }
}

function rememberPeriod(period: TopPeriod) {
  try {
    window.sessionStorage.setItem(PERIOD_KEY, period)
  } catch {
    // Storage blocked: the period still lives in the URL while on Top.
  }
}

function SparkleIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-7"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
    </svg>
  )
}

/**
 * Home feed for signed-in users (F7 §1, M3-DESIGN §6.3): tabs Nearby · Following · Top, with a
 * period dropdown on Top (Q26). The tab and period live in the URL (`?tab=top&period=month`), so
 * back and shared links keep them; changes replace the history entry.
 */
export function FeedPage() {
  const [params, setParams] = useSearchParams()
  const tab = parseTab(params.get('tab'))
  const urlPeriod = params.get('period')
  const period: TopPeriod = isPeriod(urlPeriod) ? urlPeriod : DEFAULT_TOP_PERIOD
  const options = useFeedOptions()
  const feed = useFeed(tab, period)
  const panelId = useId()
  const periodId = useId()
  const headingRef = useRef<HTMLHeadingElement>(null)

  const selectTab = (next: FeedTab) => {
    // Also covers a period that came from a shared link.
    if (tab === 'top') rememberPeriod(period)
    const search = new URLSearchParams()
    if (next !== DEFAULT_FEED_TAB) search.set('tab', next)
    if (next === 'top') {
      const keep = tab === 'top' ? period : rememberedPeriod()
      if (keep !== DEFAULT_TOP_PERIOD) search.set('period', keep)
    }
    setParams(search, { replace: true })
  }

  const selectPeriod = (event: ChangeEvent<HTMLSelectElement>) => {
    if (isPeriod(event.target.value)) rememberPeriod(event.target.value)
    setParams({ tab: 'top', period: event.target.value }, { replace: true })
    headingRef.current?.focus()
  }

  const listHeading = tab === 'top' ? periodLabel(period, options.data) : TAB_LABELS[tab]

  return (
    <FeedMain>
      <h1 className="sr-only">Good deeds</h1>
      <StoriesBar />
      <FeedNotices />
      <SegmentedTabs
        label="Feed"
        tabs={FEED_TABS.map((id) => ({ id, label: TAB_LABELS[id] }))}
        value={tab}
        onChange={selectTab}
        panelId={panelId}
      />
      {tab === 'top' && (
        <div className="flex flex-col gap-1">
          <label htmlFor={periodId} className="text-sm font-medium text-slate-800">
            Period
          </label>
          <select
            id={periodId}
            value={period}
            onChange={selectPeriod}
            className={`${inputClass} border-slate-200`}
          >
            {TOP_PERIODS.map((id) => (
              <option key={id} value={id}>
                {periodLabel(id, options.data)}
              </option>
            ))}
          </select>
        </div>
      )}
      <p className="text-sm text-slate-600">{tabIntro(tab, period, options.data)}</p>
      <section id={panelId} role="tabpanel" aria-labelledby={`${panelId}-h`}>
        <h2 id={`${panelId}-h`} ref={headingRef} tabIndex={-1} className="sr-only">
          {listHeading}
        </h2>
        <PostList
          key={tab === 'top' ? `top-${period}` : tab}
          query={feed}
          rankLabel={
            tab === 'top' ? (post) => rankChip(post.rank, period, options.data) : undefined
          }
          empty={<FeedEmpty tab={tab} onBrowseNearby={() => selectTab('nearby')} />}
          end={<FeedEnd />}
        />
      </section>
    </FeedMain>
  )
}

function FeedEmpty({ tab, onBrowseNearby }: { tab: FeedTab; onBrowseNearby: () => void }) {
  if (tab === 'following') {
    return (
      <EmptyBox
        icon={<PeopleIcon />}
        title="You’re not following anyone yet"
        body="Follow people from their deeds or profiles to see their good deeds here."
        action={
          <button type="button" className={primaryButton} onClick={onBrowseNearby}>
            Browse Nearby
          </button>
        }
      />
    )
  }
  if (tab === 'top') {
    return (
      <EmptyBox
        icon={<SparkleIcon />}
        title="Nothing here yet"
        body="The most appreciated deeds from this period will show up here."
        action={
          <button type="button" className={primaryButton} onClick={onBrowseNearby}>
            Browse Nearby
          </button>
        }
      />
    )
  }
  return (
    <EmptyBox
      icon={<SparkleIcon />}
      title="No deeds yet"
      body="Be the first: record a good deed you see today."
      action={
        <Link to="/report" className={primaryButton}>
          Record a deed
        </Link>
      }
    />
  )
}

function FeedEnd() {
  return (
    <div className="flex flex-col items-center gap-2.5 px-2 pt-5 pb-2 text-center">
      <CircleCheckIcon />
      <p className="font-semibold">You’re all caught up</p>
      <p className="text-[15px] text-slate-600">
        Saw something good today? Recognise the person while you’re both there.
      </p>
      <Link to="/report" className={secondaryButton}>
        Record a deed
      </Link>
    </div>
  )
}
