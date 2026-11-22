import { useQuery } from '@tanstack/react-query'
import { useId, useState, type ReactNode } from 'react'
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
import { TierChip } from '../scoring/TierChip'
import { BadgeList, TitlesList } from '../scoring/TitlesList'

const joinedFormat = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric' })

/**
 * `/u/:handle` (F7 "Ahmed's profile", M3-DESIGN §6.3; M4 D69, PO Q55): avatar, name, "@handle"
 * (Q22, C1), the tier, a Follow button, the counts (Verified Deeds, Badges, Hero titles,
 * Followers, Following; Q36: nobody can open another person's follower lists) and the person's
 * listed deeds. No Self-log Deeds count and never any points (D69). Signed out: the deed and
 * follow counts and "Join to follow" only (the scoring read needs a signed-in user).
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
        <Avatar name={data.display_name} handle={data.handle} size="xl" />
        <h1 className="mt-1 text-[26px] leading-tight font-bold tracking-tight break-words text-slate-900 sm:text-[32px]">
          {data.display_name}
        </h1>
        {/* Q22: "@handle" only; no city until there is more than one. */}
        <p className="text-slate-600">@{data.handle}</p>
        {user && <ProfileTier handle={data.handle} />}
        {data.bio && <p className="whitespace-pre-line text-slate-800">{data.bio}</p>}
        <p className="text-sm text-slate-500">
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
      <ProfileCounts handle={data.handle} signedIn={Boolean(user)} />
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

type ListName = 'badges' | 'titles'

/**
 * Verified Deeds · Badges · Hero titles · Followers · Following, all plain numbers (Q36, D69).
 * Badges and Hero titles open their list (M4-DESIGN §6.3). There is no Self-log Deeds count
 * and no points on a public profile (D69, PO Q55).
 */
function ProfileCounts({ handle, signedIn }: { handle: string; signedIn: boolean }) {
  const counts = useQuery({
    queryKey: deedsKeys.counts(handle),
    queryFn: () => deedsApi.deedCounts(handle),
    retry: false,
  })
  const social = useSocial(handle)
  const scoring = useUserScoring(handle, signedIn)
  const [open, setOpen] = useState<ListName | null>(null)
  const listId = useId()
  const show = (failed: boolean, n: number | undefined) => (failed ? '–' : (n ?? '…'))
  const toggle = (name: ListName) => setOpen((current) => (current === name ? null : name))
  // Five tiles fit one row from `sm`; on a phone they wrap 3 + 2 with no empty cell.
  const top = signedIn ? 'col-span-2 sm:col-span-1' : ''
  const bottom = signedIn ? 'col-span-3 sm:col-span-1' : ''
  const listButton = (name: ListName, n: number | undefined, label: string) =>
    scoring.data ? (
      <button
        type="button"
        aria-expanded={open === name}
        aria-controls={listId}
        onClick={() => toggle(name)}
        className={`${countClass} ${top} hover:bg-slate-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-700`}
      >
        <CountText n={n} label={label} />
      </button>
    ) : (
      <Count n={show(scoring.isError, n)} label={label} className={top} />
    )

  return (
    <div className="flex flex-col gap-3">
      <div
        role="group"
        aria-label="Counts"
        aria-busy={
          counts.isPending || social.isPending || (signedIn && scoring.isPending) || undefined
        }
        className={`grid gap-px overflow-hidden rounded-2xl border border-slate-200 bg-slate-200 ${
          signedIn ? 'grid-cols-6 sm:grid-cols-5' : 'grid-cols-3'
        }`}
      >
        <Count
          n={show(counts.isError, counts.data?.verified)}
          label="Verified Deeds"
          className={top}
        />
        {signedIn && listButton('badges', scoring.data?.badges_total, 'Badges')}
        {signedIn && listButton('titles', scoring.data?.titles_total, 'Hero titles')}
        <Count
          n={show(social.isError, social.data?.followers)}
          label="Followers"
          className={bottom}
        />
        <Count
          n={show(social.isError, social.data?.following)}
          label="Following"
          className={bottom}
        />
      </div>
      {open && scoring.data && (
        <section
          id={listId}
          aria-label={open === 'badges' ? 'Badges' : 'Hero titles'}
          className="rounded-2xl border border-slate-200 bg-white p-4"
        >
          {open === 'badges' ? (
            <BadgeList badges={scoring.data.badges} />
          ) : (
            <TitlesList titles={scoring.data.titles} />
          )}
        </section>
      )}
    </div>
  )
}

const countClass = 'flex flex-col items-center bg-white px-2 py-3.5'

function Count({ n, label, className = '' }: { n: ReactNode; label: string; className?: string }) {
  return (
    <div className={`${countClass} ${className}`}>
      <CountText n={n} label={label} />
    </div>
  )
}

function CountText({ n, label }: { n: ReactNode; label: string }) {
  return (
    <>
      <span className="text-[22px] leading-tight font-bold text-slate-900">{n}</span>{' '}
      <span className="text-[13px] text-slate-600">{label}</span>
    </>
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
