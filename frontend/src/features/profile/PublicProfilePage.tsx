import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
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

const joinedFormat = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric' })

/**
 * `/u/:handle` (F7 "Ahmed's profile", M3-DESIGN §6.3): avatar, name, "@handle" (Q22, C1), a
 * Follow button, four counts as plain numbers (Q36: nobody can open another person's follower
 * lists) and the person's listed deeds. Signed out: the counts and "Join to follow" only.
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
      <ProfileCounts handle={data.handle} />
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
          <strong>Verified Deeds</strong> were confirmed in person by someone who saw them.{' '}
          <strong>Self-logs</strong> are private notes: only the number is shown.
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

/** Verified Deeds · Self-log Deeds · Followers · Following, all plain numbers (Q36). */
function ProfileCounts({ handle }: { handle: string }) {
  const counts = useQuery({
    queryKey: deedsKeys.counts(handle),
    queryFn: () => deedsApi.deedCounts(handle),
    retry: false,
  })
  const social = useSocial(handle)
  const show = (failed: boolean, n: number | undefined) => (failed ? '–' : (n ?? '…'))
  return (
    <div
      role="group"
      aria-label="Counts"
      aria-busy={counts.isPending || social.isPending || undefined}
      className="grid grid-cols-2 overflow-hidden rounded-2xl border border-slate-200 bg-white sm:grid-cols-4"
    >
      <Count n={show(counts.isError, counts.data?.verified)} label="Verified Deeds" />
      <Count
        n={show(counts.isError, counts.data?.self_logged)}
        label="Self-log Deeds"
        className="border-l"
      />
      <Count
        n={show(social.isError, social.data?.followers)}
        label="Followers"
        className="border-t sm:border-t-0 sm:border-l"
      />
      <Count
        n={show(social.isError, social.data?.following)}
        label="Following"
        className="border-t border-l sm:border-t-0"
      />
    </div>
  )
}

function Count({ n, label, className = '' }: { n: ReactNode; label: string; className?: string }) {
  return (
    <div className={`flex flex-col items-center px-2 py-3.5 border-slate-200 ${className}`}>
      <span className="text-[22px] leading-tight font-bold text-slate-900">{n}</span>{' '}
      <span className="text-[13px] text-slate-600">{label}</span>
    </div>
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
