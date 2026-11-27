import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { useParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { NotFoundPage } from '../../app/NotFoundPage'
import { retryUnlessClientError } from '../feed/feedQueries'
import { AdminDialog, DialogActions, fieldClass } from './AdminDialog'
import { formatDateTime, formatLongDate } from './adminFormat'
import { AdminMain, AdminTitle } from './adminUi'
import { secondaryButton } from './ReportQueue'
import { ReviewCard } from './reviewParts'
import {
  scoringAdminApi,
  scoringAdminKeys,
  type AdminUserScoring,
  type CaseChoice,
} from './scoringAdminApi'
import { CharCount } from './scoringAdminParts'
import {
  isNotFound,
  NEEDS_CONFIRMED_CASE,
  revokeError,
  useScoringFraudOptions,
} from './scoringAdminText'

type Target = { kind: 'title' | 'badge'; id: string; label: string }

/**
 * `/admin/users/:handle` (M5 F23, D92, §6.2 "User scoring"): a person's points, titles and badges
 * (revoked ones included) with Revoke on each. Revoking needs a confirmed case about the person
 * whose decision is final; the dialog lists their confirmed cases (`#2 ?user=&status=resolved`,
 * outcome `confirmed`). 404 (unknown, the admin themself, or conflicted) shows NotFound.
 */
export function UserScoringAdminPage() {
  const { handle = '' } = useParams()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: scoringAdminKeys.user(handle),
    queryFn: () => scoringAdminApi.user(handle),
    retry: retryUnlessClientError,
  })
  const cases = useQuery({
    queryKey: scoringAdminKeys.resolvedCases(handle),
    queryFn: () => scoringAdminApi.resolvedCases(handle),
    retry: retryUnlessClientError,
    enabled: query.isSuccess,
  })
  const [target, setTarget] = useState<Target | null>(null)
  const [done, setDone] = useState<string | null>(null)

  if (query.isPending) return <Loading label="Loading this person’s scoring…" />
  if (query.isError) {
    if (isNotFound(query.error)) return <NotFoundPage />
    return (
      <AdminMain>
        <ErrorAlert>{errorMessage(query.error)}</ErrorAlert>
        <button type="button" onClick={() => void query.refetch()} className={secondaryButton}>
          Try again
        </button>
      </AdminMain>
    )
  }
  const data = query.data
  const confirmed = (cases.data?.items ?? []).filter((c) => c.outcome === 'confirmed')
  const canRevoke = cases.isSuccess && confirmed.length > 0

  const open = (next: Target) => {
    setDone(null)
    setTarget(next)
  }

  return (
    <AdminMain>
      <AdminTitle>{data.user.display_name}</AdminTitle>
      {data.user.handle && <p className="-mt-2 text-slate-600">@{data.user.handle}</p>}
      <p className="text-[15px] text-slate-800">
        Doer points <strong className="tabular-nums">{data.doer_points}</strong> · Reporter points{' '}
        <strong className="tabular-nums">{data.reporter_points}</strong>
      </p>
      {cases.isPending ? (
        <Loading label="Checking this person’s cases…" />
      ) : cases.isError ? (
        <ErrorAlert>{errorMessage(cases.error)}</ErrorAlert>
      ) : (
        !canRevoke && (
          <p className="rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-[15px] text-slate-700">
            {NEEDS_CONFIRMED_CASE}
          </p>
        )
      )}
      {done && (
        <p role="status" className="text-sm font-medium text-emerald-800">
          {done}
        </p>
      )}
      <ReviewCard title="Titles">
        {data.titles.length === 0 ? (
          <p className="text-slate-600">No titles.</p>
        ) : (
          <ul className="flex flex-col">
            {data.titles.map((t) => (
              <Item
                key={t.id}
                label={t.label}
                detail={`Place ${t.place} · ${t.region_name} · ${t.period_month}`}
                revokedAt={t.revoked_at}
                revokedReason={t.revoked_reason}
                canRevoke={canRevoke}
                onRevoke={() => open({ kind: 'title', id: t.id, label: t.label })}
              />
            ))}
          </ul>
        )}
      </ReviewCard>
      <ReviewCard title="Badges">
        {data.badges.length === 0 ? (
          <p className="text-slate-600">No badges.</p>
        ) : (
          <ul className="flex flex-col">
            {data.badges.map((b) => (
              <Item
                key={b.id}
                label={b.label}
                detail={`Awarded ${formatLongDate(b.awarded_at)}`}
                revokedAt={b.revoked_at}
                revokedReason={b.revoked_reason}
                canRevoke={canRevoke}
                onRevoke={() => open({ kind: 'badge', id: b.id, label: b.label })}
              />
            ))}
          </ul>
        )}
      </ReviewCard>
      {target && (
        <RevokeDialog
          target={target}
          cases={confirmed}
          onSaved={(view) => {
            queryClient.setQueryData(scoringAdminKeys.user(handle), view)
            setTarget(null)
            setDone(`Revoked: ${target.label}.`)
          }}
          onStale={() => void query.refetch()}
          onCancel={() => setTarget(null)}
        />
      )}
    </AdminMain>
  )
}

function Item({
  label,
  detail,
  revokedAt,
  revokedReason,
  canRevoke,
  onRevoke,
}: {
  label: string
  detail: string
  revokedAt: string | null
  revokedReason: string | null
  canRevoke: boolean
  onRevoke: () => void
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 py-2.5 first:border-0 first:pt-0">
      <div className="min-w-0">
        <p className="font-semibold break-words">{label}</p>
        <p className="text-sm text-slate-600">{detail}</p>
        {revokedAt && (
          <p className="text-sm font-medium text-red-700">
            Revoked {formatDateTime(revokedAt)}
            {revokedReason ? `: ${revokedReason}` : ''}
          </p>
        )}
      </div>
      {!revokedAt && (
        <button
          type="button"
          onClick={onRevoke}
          disabled={!canRevoke}
          aria-label={`Revoke ${label}`}
          className={`${secondaryButton} disabled:cursor-not-allowed disabled:opacity-60`}
        >
          Revoke
        </button>
      )}
    </li>
  )
}

function RevokeDialog({
  target,
  cases,
  onSaved,
  onStale,
  onCancel,
}: {
  target: Target
  cases: CaseChoice[]
  onSaved: (view: AdminUserScoring) => void
  onStale: () => void
  onCancel: () => void
}) {
  const options = useScoringFraudOptions()
  const noteMin = options.data?.note_min_chars ?? 5
  const noteMax = options.data?.note_max_chars ?? 1000
  const caseName = useId()
  const noteId = useId()
  const countId = useId()
  const [chosen, setChosen] = useState(cases[0]?.case_id ?? '')
  const [note, setNote] = useState('')
  const [tooShort, setTooShort] = useState(false)
  const mutation = useMutation({
    mutationFn: () =>
      target.kind === 'title'
        ? scoringAdminApi.revokeTitle(target.id, chosen, note.trim())
        : scoringAdminApi.revokeBadge(target.id, chosen, note.trim()),
    onSuccess: onSaved,
    onError: (error) => {
      if (isNotFound(error) || (error instanceof ApiError && error.code === 'already_revoked')) {
        onStale()
      }
    },
  })
  const busy = mutation.isPending
  return (
    <AdminDialog title={`Revoke “${target.label}”?`} onDismiss={onCancel} busy={busy}>
      {({ bodyId }) => (
        <>
          <p id={bodyId} className="text-slate-700">
            {target.kind === 'title'
              ? 'The title is removed from their profile. It isn’t passed to anyone else, and the final leaderboard stays as it is.'
              : 'The badge is removed from their profile.'}
          </p>
          {/* A radio list, not a select: case summaries are long and a native option clips them. */}
          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-[15px] font-semibold">Confirmed case</legend>
            {cases.map((c) => (
              <label
                key={c.case_id}
                className={`flex w-full cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-brand-700 ${
                  chosen === c.case_id
                    ? 'border-brand-700 bg-brand-50'
                    : 'border-slate-300 bg-white hover:bg-slate-50'
                }`}
              >
                <input
                  type="radio"
                  name={caseName}
                  value={c.case_id}
                  checked={chosen === c.case_id}
                  onChange={() => setChosen(c.case_id)}
                  data-autofocus={chosen === c.case_id ? '' : undefined}
                  className="mt-1 size-4 shrink-0 accent-brand-700"
                />
                <span className="flex min-w-0 flex-col">
                  <span className="text-[15px] break-words text-slate-900">{c.summary}</span>
                  {c.resolved_at && (
                    <span className="text-sm text-slate-600">
                      decided {formatLongDate(c.resolved_at)}
                    </span>
                  )}
                </span>
              </label>
            ))}
          </fieldset>
          <div className="flex flex-col gap-1">
            <label htmlFor={noteId} className="text-[15px] font-semibold">
              Note for the record <span className="font-normal text-slate-600">(required)</span>
            </label>
            <textarea
              id={noteId}
              rows={3}
              value={note}
              maxLength={noteMax}
              onChange={(e) => setNote(e.target.value)}
              aria-describedby={countId}
              aria-invalid={tooShort || undefined}
              className={fieldClass}
            />
            <CharCount id={countId} value={note} max={noteMax} />
          </div>
          {(tooShort || mutation.isError) && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {tooShort
                ? `Add a note of at least ${noteMin} characters.`
                : revokeError(mutation.error)}
            </p>
          )}
          <DialogActions
            confirm={busy ? 'Revoking…' : 'Revoke'}
            danger
            busy={busy}
            onConfirm={() => {
              if (note.trim().length < noteMin) {
                setTooShort(true)
                return
              }
              setTooShort(false)
              mutation.mutate()
            }}
            onCancel={onCancel}
          />
        </>
      )}
    </AdminDialog>
  )
}
