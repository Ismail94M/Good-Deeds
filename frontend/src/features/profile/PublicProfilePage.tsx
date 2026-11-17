import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router'
import { ErrorAlert, LinkButton, Loading, Page } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { identityApi, queryKeys } from '../auth/identityApi'
import { DeedCounts } from './DeedCounts'

const joinedFormat = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric' })

export function PublicProfilePage() {
  const { handle = '' } = useParams()
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
    <Page title={data.display_name}>
      <section className="space-y-2 rounded-2xl border border-slate-200 bg-white p-4">
        {/* Q22: "@handle" only; no city until there is more than one. */}
        <p className="text-slate-600">@{data.handle}</p>
        {data.bio && <p className="whitespace-pre-line">{data.bio}</p>}
        <p className="text-sm text-slate-500">
          Joined {joinedFormat.format(new Date(data.joined_at))}
        </p>
      </section>
      <DeedCounts handle={data.handle} own={false} />
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
    </Page>
  )
}
