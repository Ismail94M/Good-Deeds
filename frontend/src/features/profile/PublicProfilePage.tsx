import { useQuery } from '@tanstack/react-query'
import { useId, useRef, useState, type ReactNode } from 'react'
import { useParams } from 'react-router'
import { ErrorAlert, LinkButton, Loading, Page } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { identityApi, queryKeys } from '../auth/identityApi'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'
import { useSocial, useUserDeeds } from '../feed/feedQueries'
import { firstName } from '../feed/feedText'
import { Avatar, EmptyBox, FeedMain, FollowButton } from '../feed/feedUi'
import { useOnboardingGate } from '../feed/OnboardingPrompt'
import { PostList } from '../feed/PostList'
import { useToggleFollow } from '../feed/postActions'
import { useToast } from '../feed/useToast'
import { useUserScoring } from '../scoring/scoringQueries'
import { CrownIcon } from '../scoring/HeroTitles'
import { useCityRank } from '../scoring/ranks'
import { PodiumIcon, RankingsButton } from '../scoring/Rankings'
import { TierChip } from '../scoring/TierChip'
import { BadgeList, TitlesList } from '../scoring/TitlesList'

const joinedFormat = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric' })

/**
 * `/u/:handle` (F7 "Ahmed's profile", M3-DESIGN §6.3; M4 D69, PO Q55): avatar, name, "@handle"
 * (Q22, C1), the tier, a Follow button, the counts (Verified Deeds, Badges, Hero titles,
 * Followers, Following; Q36: nobody can open another person's follower lists) and the person's
 * listed deeds. No Self-log Deeds count and never any points (D69). Signed out: the tier and
 * all five counts (the scoring read #4 is public, §3.4 item 4) and "Join to follow", no deeds.
 */
export function PublicProfilePage() {
  const { handle = '' } = useParams()
  const { user } = useAuth()
  const { data, error, isPending } = useQuery({
    queryKey: queryKeys.profile(handle),
    queryFn: () => identityApi.profile(handle),
    retry: false,
  })

  if (isPending) return <Loading label="Loading profile…" />

  if (error) {
    if (error instanceof ApiError && error.status === 404) {
      return (
        <Page title="Profile not found" intro="We couldn't find anyone with that handle.">
          <p className="text-slate-600">
            Check the spelling, or ask them to share their link again.
          </p>
          <LinkButton to="/" variant="secondary">
            Go home
          </LinkButton>
        </Page>
      )
    }
    return (
      <Page title="Profile">
        <ErrorAlert>{errorMessage(error)}</ErrorAlert>
      </Page>
    )
  }

  return (
    <FeedMain>
      <section className="flex flex-col items-center gap-2 pt-2 text-center">
        <span className="rounded-full bg-white p-1 shadow-[0_0_0_1px_#e2e8f0,0_8px_20px_-10px_rgba(36,66,184,0.45)]">
          <Avatar name={data.display_name} handle={data.handle} size="xl" />
        </span>
        <h1 className="mt-1 text-[26px] leading-tight font-bold tracking-tight break-words text-slate-900 sm:text-[32px]">
          {data.display_name}
        </h1>
        {/* Q22: "@handle" only; no city until there is more than one. */}
        <p className="text-slate-600">@{data.handle}</p>
        <ProfileTier handle={data.handle} />
        {data.bio && (
          <p className="mt-1.5 max-w-md text-[15px] leading-relaxed whitespace-pre-line text-slate-800">
            {data.bio}
          </p>
        )}
        <p className="inline-flex items-center gap-1.5 text-[13px] text-slate-500">
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <rect x="3.5" y="5" width="17" height="15" rx="2.5" />
            <path d="M3.5 10h17M8 3v4M16 3v4" />
          </svg>
          Joined {joinedFormat.format(new Date(data.joined_at))}
        </p>
        {user ? (
          <ProfileFollow handle={data.handle} name={data.display_name} />
        ) : (
          <LinkButton
            to={`/signup?next=${encodeURIComponent(`/u/${data.handle}`)}`}
            className="mt-1.5 min-w-40 rounded-full"
          >
            Join to follow
          </LinkButton>
        )}
      </section>
      <ProfileCounts handle={data.handle} name={data.display_name} />
      <p className="flex items-start gap-2.5 text-sm text-slate-600">
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="mt-px size-5 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5M12 8h.01" />
        </svg>
        <span>
          <strong>Verified Deeds</strong> were confirmed in person by someone who saw them.
        </span>
      </p>
      {user && <ProfileDeeds handle={data.handle} name={data.display_name} />}
    </FeedMain>
  )
}

function ProfileFollow({ handle, name }: { handle: string; name: string }) {
  const { user } = useAuth()
  const social = useSocial(handle)
  const follow = useToggleFollow(user?.handle)
  const gate = useOnboardingGate()
  const [toast, showToast] = useToast()
  if (!social.data || social.data.is_me) return null
  const following = Boolean(social.data.is_following)
  return (
    <div className="mt-1.5">
      <FollowButton
        large
        following={following}
        name={name}
        onClick={() =>
          gate.guard(() =>
            follow.mutate(
              { handle, follow: !following },
              {
                onError: (error) => {
                  if (!gate.handleError(error)) showToast('That didn’t work. Please try again.')
                },
              },
            ),
          )
        }
      />
      {gate.prompt}
      {toast}
    </div>
  )
}

/** The public tier chip (D69). Hidden until it loads, and on any error. */
function ProfileTier({ handle }: { handle: string }) {
  const scoring = useUserScoring(handle)
  if (!scoring.data) return null
  return <TierChip tier={scoring.data.tier} />
}

type Tone = 'emerald' | 'amber' | 'rose' | 'violet' | 'sky' | 'brand'

const TONE: Record<Tone, string> = {
  emerald: 'bg-emerald-50 text-emerald-600',
  rose: 'bg-rose-50 text-rose-600',
  violet: 'bg-violet-50 text-violet-600',
  sky: 'bg-sky-50 text-sky-600',
  amber: 'bg-amber-50 text-amber-700',
  brand: 'bg-brand-50 text-brand-700',
}

/**
 * Verified Deeds · Badges · Hero titles · Followers · Following as stat tiles, each with its own
 * icon and soft colour (PO 2026-11-24, D75). Badges opens the badge medals; Hero titles jumps to
 * the title cards below. Followers and Following stay plain numbers (Q36), and there is no
 * Self-log Deeds count and no points on a public profile (D69, PO Q55). Phone: the three
 * achievement stats on top, the two social ones below; from `sm`, all five in one row.
 *
 * Signed in (D76): a City Rank tile beside Verified Deeds, from the same snapshots as the City
 * board, and "View all rankings" below; six tiles, three a row on a phone, one row from `sm`.
 * Visitors never see ranks: the boards they come from need sign-in.
 */
function ProfileCounts({ handle, name }: { handle: string; name: string }) {
  const { user } = useAuth()
  const signedIn = user !== null
  const counts = useQuery({
    queryKey: deedsKeys.counts(handle),
    queryFn: () => deedsApi.deedCounts(handle),
    retry: false,
  })
  const social = useSocial(handle)
  const scoring = useUserScoring(handle)
  const [badgesOpen, setBadgesOpen] = useState(false)
  const badgesId = useId()
  const titlesHeading = useRef<HTMLHeadingElement>(null)
  const cityRank = useCityRank(handle, signedIn)
  const show = (failed: boolean, n: number | undefined) => (failed ? '–' : (n ?? '…'))
  const top = signedIn ? '' : 'col-span-2 sm:col-span-1'
  const bottom = signedIn ? '' : 'col-span-3 sm:col-span-1'
  const titles = scoring.data?.titles ?? []

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2.5">
        <div
          role="group"
          aria-label="Counts"
          aria-busy={counts.isPending || social.isPending || scoring.isPending || undefined}
          className={
            signedIn
              ? 'grid grid-cols-3 gap-2 sm:grid-cols-6'
              : 'grid grid-cols-6 gap-2 sm:grid-cols-5'
          }
        >
          <Stat
            icon={<ShieldIcon />}
            tone="emerald"
            n={show(counts.isError, counts.data?.verified)}
            label="Verified Deeds"
            className={top}
          />
          {signedIn && (
            <Stat
              icon={<PodiumIcon />}
              tone="amber"
              n={cityRank.value}
              label="City Rank"
              hint={cityRank.unranked ? 'Not ranked yet this month' : undefined}
              className={top}
            />
          )}
          <Stat
            icon={<MedalIcon />}
            tone="rose"
            n={show(scoring.isError, scoring.data?.badges_total)}
            label="Badges"
            className={top}
            button={
              scoring.data
                ? {
                    onClick: () => setBadgesOpen((v) => !v),
                    expanded: badgesOpen,
                    controls: badgesId,
                  }
                : undefined
            }
          />
          <Stat
            icon={<CrownIcon className="size-[18px]" />}
            tone="violet"
            n={show(scoring.isError, scoring.data?.titles_total)}
            label="Hero titles"
            className={top}
            button={
              titles.length > 0
                ? {
                    onClick: () => {
                      const heading = titlesHeading.current
                      heading?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
                      heading?.focus({ preventScroll: true })
                    },
                  }
                : undefined
            }
          />
          <Stat
            icon={<PeopleIcon />}
            tone="sky"
            n={show(social.isError, social.data?.followers)}
            label="Followers"
            className={bottom}
            row={!signedIn}
          />
          <Stat
            icon={<FollowingIcon />}
            tone="brand"
            n={show(social.isError, social.data?.following)}
            label="Following"
            className={bottom}
            row={!signedIn}
          />
        </div>
        {signedIn && <RankingsButton handle={handle} title={`${name}’s rankings`} />}
        {badgesOpen && scoring.data && (
          <section
            id={badgesId}
            aria-label="Badges"
            className="rounded-2xl border border-slate-200 bg-white p-4"
          >
            <BadgeList badges={scoring.data.badges} />
          </section>
        )}
      </div>
      {titles.length > 0 && (
        <section aria-labelledby="hero-titles-h" className="flex scroll-mt-20 flex-col gap-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <h2
              id="hero-titles-h"
              ref={titlesHeading}
              tabIndex={-1}
              className="text-lg font-extrabold tracking-tight text-slate-900 focus:outline-none"
            >
              Kindness Hero titles
            </h2>
            <span className="text-sm font-semibold text-slate-500">{titles.length} earned</span>
          </div>
          <TitlesList titles={titles} />
        </section>
      )}
    </div>
  )
}

function Stat({
  icon,
  tone,
  n,
  label,
  className,
  hint,
  row = false,
  button,
}: {
  icon: ReactNode
  tone: Tone
  n: ReactNode
  label: string
  className: string
  /** Spoken after the label, e.g. "Not ranked yet this month" for a "–" rank. */
  hint?: string
  /** Icon beside the number on a phone (the social stats), above it from `sm`. */
  row?: boolean
  button?: { onClick: () => void; expanded?: boolean; controls?: string }
}) {
  const layout = row
    ? 'flex-row items-center gap-2.5 sm:flex-col sm:items-start sm:gap-1.5'
    : 'flex-col items-start gap-1.5'
  const body = (
    <>
      <span
        aria-hidden
        className={`flex size-8 shrink-0 items-center justify-center rounded-[10px] ${TONE[tone]}`}
      >
        {icon}
      </span>
      <span className="flex flex-col gap-1">
        <span
          className={`leading-none font-extrabold tracking-tight text-slate-900 ${row ? 'text-xl sm:text-2xl' : 'text-2xl'}`}
        >
          {n}
        </span>{' '}
        <span className="text-[12.5px] leading-tight font-medium text-slate-600">{label}</span>
        {hint && <span className="sr-only">, {hint}</span>}
      </span>
    </>
  )
  const base = `flex min-h-11 rounded-2xl border border-slate-200 bg-white p-3 text-left ${layout} ${className}`
  if (!button) return <div className={base}>{body}</div>
  return (
    <button
      type="button"
      onClick={button.onClick}
      aria-expanded={button.expanded}
      aria-controls={button.controls}
      className={`${base} transition-shadow hover:border-slate-300 hover:shadow-[0_2px_8px_-4px_rgba(15,23,42,0.18)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700 ${
        button.expanded ? 'border-rose-300 ring-3 ring-rose-100' : ''
      }`}
    >
      {body}
    </button>
  )
}

const iconProps = {
  'aria-hidden': true,
  viewBox: '0 0 24 24',
  className: 'size-[18px]',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2.2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const

function ShieldIcon() {
  return (
    <svg {...iconProps}>
      <path d="M12 3l7 3v5.5c0 4.3-3 7.9-7 9.5-4-1.6-7-5.2-7-9.5V6z" />
      <path d="M8.8 12.2l2.2 2.2 4.3-4.4" />
    </svg>
  )
}

function MedalIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="9" r="5.5" />
      <path d="M8.6 13.3L7 21l5-2.6 5 2.6-1.6-7.7" />
    </svg>
  )
}

function PeopleIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="9" cy="8.5" r="3.5" />
      <path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5" />
      <path d="M15.5 5.2a3.4 3.4 0 0 1 0 6.6M18 14.8c2 .7 3.2 2.4 3.5 5.2" />
    </svg>
  )
}

function FollowingIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="10" cy="8.5" r="3.5" />
      <path d="M3.5 20c.6-3.6 3.2-5.5 6.5-5.5 1.6 0 3 .4 4.1 1.2" />
      <path d="M15.5 18l2 2 4-4.2" />
    </svg>
  )
}

function ProfileDeeds({ handle, name }: { handle: string; name: string }) {
  const deeds = useUserDeeds(handle)
  return (
    <section className="flex flex-col gap-4" aria-labelledby="profile-deeds-h">
      <h2 id="profile-deeds-h" className="mt-1 text-lg font-bold text-slate-900">
        {firstName(name)}’s deeds
      </h2>
      <PostList
        query={deeds}
        loadingLabel="Loading deeds"
        empty={
          <EmptyBox
            icon={
              <svg
                aria-hidden
                viewBox="0 0 24 24"
                className="size-7"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
            }
            title="No deeds in the feed yet"
            body="Verified deeds about this person show up here."
          />
        }
      />
    </section>
  )
}
