import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ErrorAlert, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { useDeedOptions } from '../deeds/deedQueries'
import { deedsApi, deedsKeys, type SelfLogOut } from '../deeds/deedsApi'
import { categoryLabel } from '../deeds/deedText'
import {
  BackLink,
  EmptyState,
  F6Main,
  F6Title,
  LockIcon,
  PlusIcon,
  primaryButtonClass,
  secondaryButtonClass,
  smallButtonClass,
  smallDangerButtonClass,
} from '../deeds/deedUi'
import { formatCardDate } from '../report/format'

/**
 * `/me/self-logs` (F6 canvas): the private diary. Each item has Edit and Delete; delete asks
 * first. Self-logs never earn points and have no verify button (D3).
 */
export function SelfLogsPage() {
  const options = useDeedOptions()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [deleting, setDeleting] = useState<SelfLogOut | null>(null)
  const list = useInfiniteQuery({
    queryKey: deedsKeys.selfLogs,
    queryFn: ({ pageParam }) => deedsApi.selfLogs(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const remove = useMutation({
    mutationFn: (id: string) => deedsApi.deleteSelfLog(id),
    onSuccess: () => {
      setDeleting(null)
      void queryClient.invalidateQueries({ queryKey: deedsKeys.selfLogs })
      if (user) void queryClient.invalidateQueries({ queryKey: deedsKeys.counts(user.handle) })
    },
  })

  let content
  if (list.isPending) content = <Loading label="Loading your self-logs…" />
  else if (list.isError) {
    content = (
      <div className="flex flex-col items-start gap-3">
        <ErrorAlert>{errorMessage(list.error)}</ErrorAlert>
        <button type="button" onClick={() => void list.refetch()} className={secondaryButtonClass}>
          Try again
        </button>
      </div>
    )
  } else {
    const logs = list.data.pages.flatMap((page) => page.items)
    content =
      logs.length === 0 ? (
        <EmptyState
          icon={
            <svg
              viewBox="0 0 24 24"
              className="size-7"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z" />
              <path d="M5 17a3 3 0 0 1 3-3h11" />
            </svg>
          }
          title="No self-logs yet"
          body="Keep a private note of the good things you do, even when no one is watching."
        />
      ) : (
        <>
          <ul className="flex flex-col gap-3">
            {logs.map((log) => (
              <li key={log.id}>
                <article className="flex flex-col gap-1.5 rounded-2xl border border-slate-200 bg-white px-4 py-3.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-[13px] font-semibold text-slate-600">
                      <LockIcon className="size-3.5" />
                      Only you
                    </span>
                    <span className="text-[13px] text-[#586579]">
                      {formatCardDate(log.occurred_at)}
                    </span>
                  </div>
                  <h2 className="text-sm font-semibold text-brand-900">
                    {categoryLabel(options.data?.categories, log.category)}
                  </h2>
                  <p className="text-[15px] break-words whitespace-pre-line text-slate-800">
                    {log.description}
                  </p>
                  {log.has_location && <p className="text-sm text-[#586579]">Location added</p>}
                  <div className="mt-1 grid grid-cols-2 gap-2 border-t border-slate-200 pt-2.5">
                    <Link
                      to={`/me/self-logs/${encodeURIComponent(log.id)}/edit`}
                      className={smallButtonClass}
                      aria-label={`Edit self-log from ${formatCardDate(log.occurred_at)}`}
                    >
                      Edit
                    </Link>
                    <button
                      type="button"
                      onClick={() => {
                        remove.reset()
                        setDeleting(log)
                      }}
                      className={smallDangerButtonClass}
                      aria-label={`Delete self-log from ${formatCardDate(log.occurred_at)}`}
                    >
                      Delete
                    </button>
                  </div>
                </article>
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
        </>
      )
  }

  return (
    <F6Main>
      <BackLink to="/profile" label="Profile" />
      <F6Title>Self-logs</F6Title>
      <p className="flex items-start gap-2.5 rounded-xl bg-brand-50 px-3.5 py-3 text-[15px] text-brand-900">
        <LockIcon className="mt-0.5 size-5 shrink-0" />
        <span>
          Self-logs are private and don’t earn points. If someone saw you, ask them to record it by
          scanning your QR.
        </span>
      </p>
      <Link to="/me/self-logs/new" className={primaryButtonClass}>
        <PlusIcon />
        Log a deed
      </Link>
      {content}
      {deleting && (
        <ConfirmDialog
          title="Delete this self-log?"
          body="It’s removed for good. Self-logs don’t affect your points."
          onDismiss={() => setDeleting(null)}
          busy={remove.isPending}
          error={remove.error ? errorMessage(remove.error) : null}
          actions={[
            {
              label: remove.isPending ? 'Deleting…' : 'Delete',
              onClick: () => remove.mutate(deleting.id),
              variant: 'danger',
            },
            {
              label: 'Keep it',
              onClick: () => setDeleting(null),
              variant: 'secondary',
              autoFocus: true,
            },
          ]}
        />
      )}
    </F6Main>
  )
}
