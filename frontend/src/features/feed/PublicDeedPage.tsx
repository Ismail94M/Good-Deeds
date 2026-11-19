import { useEffect } from 'react'
import { Link, Navigate, useParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { usePublicDeed } from './feedQueries'
import { firstName } from './feedText'
import { FeedMain, focusRing, primaryButton, secondaryButton } from './feedUi'
import { DeedUnavailable } from './DeedPage'
import { PublicPostCard } from './PostCard'

/**
 * `/d/:id`, the public share page (F7 §3, M3-DESIGN §6.3). A signed-in visitor goes straight to
 * the member page `/deeds/:id`. Visitors see one deed without action buttons, and everything
 * that needs an account (names, likes, comments, Join) leads to sign-up with `next=/deeds/:id`.
 * Hidden, withdrawn, removed and missing deeds all show the same unavailable state.
 */
export function PublicDeedPage() {
  const { id = '' } = useParams()
  const { user, isLoading } = useAuth()
  if (isLoading) return <Loading />
  if (user) return <Navigate to={`/deeds/${encodeURIComponent(id)}`} replace />
  return <PublicDeed id={id} />
}

/** `<meta name="robots" content="noindex, nofollow">` while the share page is open (Q40). */
function useNoIndex() {
  useEffect(() => {
    const meta = document.createElement('meta')
    meta.name = 'robots'
    meta.content = 'noindex, nofollow'
    document.head.appendChild(meta)
    return () => meta.remove()
  }, [])
}

function PublicDeed({ id }: { id: string }) {
  useNoIndex()
  const deed = usePublicDeed(id)
  const next = encodeURIComponent(`/deeds/${encodeURIComponent(id)}`)
  const joinHref = `/signup?next=${next}`
  const loginHref = `/login?next=${next}`

  if (deed.isPending) return <Loading label="Loading the deed…" />
  if (deed.isError) {
    if (deed.error instanceof ApiError && deed.error.status === 404) {
      return (
        <FeedMain>
          <DeedUnavailable action={{ to: '/', label: 'Discover Good Deeds' }} />
        </FeedMain>
      )
    }
    return (
      <FeedMain>
        <ErrorAlert>{errorMessage(deed.error)}</ErrorAlert>
        <button
          type="button"
          className={`${secondaryButton} self-start`}
          onClick={() => void deed.refetch()}
        >
          Try again
        </button>
      </FeedMain>
    )
  }

  const data = deed.data
  return (
    <FeedMain>
      <h1 className="sr-only">A good deed on Good Deeds</h1>
      <p className="rounded-xl bg-brand-50 px-4 py-3 text-[15px] text-brand-900">
        Someone shared this good deed with you.
      </p>
      <PublicPostCard deed={data} joinHref={joinHref} />
      <section
        aria-labelledby="join-h"
        className="flex flex-col gap-2.5 rounded-2xl bg-brand-50 p-4.5"
      >
        <h2 id="join-h" className="text-lg font-bold text-slate-900">
          See more good deeds like this
        </h2>
        <p className="text-slate-700">
          Join Good Deeds to like, comment, follow {firstName(data.doer.display_name)} and explore
          other people’s deeds.
        </p>
        <div className="mt-1 grid grid-cols-2 gap-2">
          <Link to={joinHref} className={primaryButton}>
            Join Good Deeds
          </Link>
          <Link
            to={loginHref}
            className={`inline-flex min-h-12 items-center justify-center rounded-lg border border-indigo-200 bg-white font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`}
          >
            Sign in
          </Link>
        </div>
      </section>
      <p className="text-sm text-slate-600">
        Good Deeds only shows deeds confirmed in person by someone who saw them. Exact locations and
        contact details are never shown.
      </p>
    </FeedMain>
  )
}
