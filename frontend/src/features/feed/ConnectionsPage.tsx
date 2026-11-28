import { useId } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { BackLink } from '../deeds/deedUi'
import type { ConnectionOut } from './feedApi'
import { useConnections, useRemoveFollower, useSocial } from './feedQueries'
import { Avatar, FeedMain, FollowButton, focusRing, SegmentedTabs, secondaryButton } from './feedUi'
import { useOnboardingGate } from './OnboardingPrompt'
import { useToggleFollow } from './postActions'
import { useToast } from './useToast'

type ListName = 'followers' | 'following'

/**
 * `/me/connections?tab=followers|following` (F7 "Your followers"): my own lists only (Q36).
 * Followers can be removed quietly; people I follow can be unfollowed and followed again.
 */
export function ConnectionsPage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const tab: ListName = params.get('tab') === 'following' ? 'following' : 'followers'
  const social = useSocial(user?.handle ?? '')
  const panelId = useId()
  if (!user) return <Loading />

  const count = (n: number | undefined) => (n === undefined ? '' : ` · ${n}`)
  return (
    <FeedMain>
      <BackLink to="/profile" label="Profile" />
      <h1 className="text-[26px] leading-tight font-bold tracking-tight break-words text-slate-900 sm:text-[32px]">
        {user.display_name}
      </h1>
      <SegmentedTabs
        label="Connections"
        panelId={panelId}
        value={tab}
        onChange={(next) => setParams({ tab: next }, { replace: true })}
        tabs={[
          { id: 'followers', label: `Followers${count(social.data?.followers)}` },
          { id: 'following', label: `Following${count(social.data?.following)}` },
        ]}
      />
      <div id={panelId} role="tabpanel">
        <ConnectionList key={tab} list={tab} />
      </div>
      {tab === 'followers' && (
        <p className="text-sm text-slate-600">
          Removing a follower is quiet: they aren’t told, and they can follow you again later.
        </p>
      )}
    </FeedMain>
  )
}

function ConnectionList({ list }: { list: ListName }) {
  const { user } = useAuth()
  const query = useConnections(list)
  const remove = useRemoveFollower()
  const follow = useToggleFollow(user?.handle)
  const gate = useOnboardingGate()
  const [toast, showToast] = useToast()
  const failed = (error: unknown) => {
    if (!gate.handleError(error)) showToast('That didn’t work. Please try again.')
  }

  if (query.isPending) return <Loading label="Loading…" />
  if (query.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <ErrorAlert>{errorMessage(query.error)}</ErrorAlert>
        <button type="button" className={secondaryButton} onClick={() => void query.refetch()}>
          Try again
        </button>
      </div>
    )
  }
  const rows = query.data.pages.flatMap((page) => page.items)
  if (rows.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-slate-600">
        {list === 'followers'
          ? 'No followers yet. When someone follows you, they show up here.'
          : 'You’re not following anyone yet.'}
      </p>
    )
  }

  const action = (row: ConnectionOut) =>
    list === 'followers' ? (
      <button
        type="button"
        aria-label={`Remove ${row.user.display_name} from your followers`}
        disabled={remove.isPending && remove.variables === row.user.handle}
        onClick={() =>
          gate.guard(() =>
            remove.mutate(row.user.handle, {
              onSuccess: () => showToast(`${row.user.display_name} was removed.`),
              onError: failed,
            }),
          )
        }
        className={`min-h-11 shrink-0 rounded-full border border-slate-300 bg-white px-3.5 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60 ${focusRing}`}
      >
        Remove
      </button>
    ) : (
      <FollowButton
        following={row.following}
        name={row.user.display_name}
        onClick={() =>
          gate.guard(() =>
            follow.mutate({ handle: row.user.handle, follow: !row.following }, { onError: failed }),
          )
        }
      />
    )

  return (
    <>
      <ul className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        {rows.map((row, index) => (
          <li
            key={row.user.handle}
            className={`flex items-center gap-3 px-3.5 py-2.5 ${index > 0 ? 'border-t border-slate-200' : ''}`}
          >
            <Avatar user={row.user} size="md" />
            <Link
              to={`/u/${encodeURIComponent(row.user.handle)}`}
              className={`min-w-0 grow rounded ${focusRing}`}
            >
              <span className="block font-semibold break-words text-slate-900">
                {row.user.display_name}
              </span>
              <span className="block text-sm text-slate-600">@{row.user.handle}</span>
            </Link>
            {action(row)}
          </li>
        ))}
      </ul>
      {query.hasNextPage && (
        <button
          type="button"
          className={`${secondaryButton} mt-3`}
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? 'Loading…' : 'Show more'}
        </button>
      )}
      {gate.prompt}
      {toast}
    </>
  )
}
