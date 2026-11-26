import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { NotFoundPage } from '../../app/NotFoundPage'
import { retryUnlessClientError } from '../feed/feedQueries'
import { fieldClass } from './AdminDialog'
import { formatDateTime } from './adminFormat'
import { AdminMain, AdminTitle, focusRing } from './adminUi'
import { ReviewCard } from './reviewParts'
import {
  scoringAdminApi,
  scoringAdminKeys,
  type AdminDeedScoring,
  type AdminUserRef,
  type FraudOptionsForScoring,
  type ScoringRole,
} from './scoringAdminApi'
import { Banner, CharCount } from './scoringAdminParts'
import {
  FINAL_MONTH,
  isNotFound,
  overrideError,
  personName,
  roleLabel,
  secondAdminSent,
  useScoringFraudOptions,
} from './scoringAdminText'

const primaryButton = `inline-flex min-h-12 items-center justify-center rounded-lg bg-brand-700 px-5 text-base font-semibold text-white hover:bg-brand-900 disabled:opacity-60 ${focusRing}`

/**
 * `/admin/scoring/deeds/:deedId` (M5 F22, §6.2 "Deed scoring"): the M4 #9 ledger, a "Held for
 * review" banner while a confirmed decision holds the deed, the overrides history and the
 * override form. A big cut comes back as 202 and waits for a second admin (Q77). 404 (unknown
 * deed, or a conflicted admin) shows NotFound.
 */
export function DeedScoringAdminPage() {
  const { deedId = '' } = useParams()
  const query = useQuery({
    queryKey: scoringAdminKeys.deed(deedId),
    queryFn: () => scoringAdminApi.deed(deedId),
    retry: retryUnlessClientError,
  })
  const options = useScoringFraudOptions()

  if (query.isPending) return <Loading label="Loading this deed’s points…" />
  if (query.isError) {
    if (isNotFound(query.error)) return <NotFoundPage />
    return (
      <AdminMain>
        <ErrorAlert>{errorMessage(query.error)}</ErrorAlert>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className={`min-h-11 self-start rounded-lg border border-slate-300 bg-white px-4 font-semibold ${focusRing}`}
        >
          Try again
        </button>
      </AdminMain>
    )
  }
  const scoring = query.data
  return (
    <AdminMain>
      <AdminTitle>Deed points</AdminTitle>
      <p className="text-sm break-all text-slate-600">Deed {scoring.deed_id}</p>
      {scoring.held && <Banner tone="amber">Held for review</Banner>}
      {scoring.month_final && <Banner tone="blue">{FINAL_MONTH}</Banner>}
      <People scoring={scoring} />
      <LedgerTable scoring={scoring} />
      <OverridesHistory scoring={scoring} reasons={options.data?.override_reason_codes} />
      {options.isError ? (
        <ErrorAlert>{errorMessage(options.error)}</ErrorAlert>
      ) : options.data ? (
        <OverrideForm deedId={scoring.deed_id} options={options.data} />
      ) : (
        <Loading label="Loading the form…" />
      )}
    </AdminMain>
  )
}

function PersonLink({ user }: { user: AdminUserRef | null }) {
  if (!user?.handle) return <>{personName(user)}</>
  return (
    <Link
      to={`/admin/users/${encodeURIComponent(user.handle)}`}
      className={`font-semibold text-brand-700 underline-offset-2 hover:underline ${focusRing}`}
    >
      {user.display_name}
    </Link>
  )
}

function People({ scoring }: { scoring: AdminDeedScoring }) {
  const row = scoring.ledger.find((entry) => entry.doer || entry.reporter)
  if (!row) return null
  return (
    <p className="text-[15px] text-slate-700">
      {row.category_label} · Reporter <PersonLink user={row.reporter} /> → Doer{' '}
      <PersonLink user={row.doer} />
    </p>
  )
}

function LedgerTable({ scoring }: { scoring: AdminDeedScoring }) {
  return (
    <ReviewCard title="Points ledger">
      {scoring.ledger.length === 0 ? (
        <p className="text-slate-600">No points rows for this deed yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="text-slate-600">
              <tr>
                <th scope="col" className="py-1.5 pr-3 font-semibold">
                  When
                </th>
                <th scope="col" className="py-1.5 pr-3 font-semibold">
                  Role
                </th>
                <th scope="col" className="py-1.5 pr-3 font-semibold">
                  Kind
                </th>
                <th scope="col" className="py-1.5 pr-3 text-right font-semibold">
                  Points
                </th>
                <th scope="col" className="py-1.5 font-semibold">
                  Reason
                </th>
              </tr>
            </thead>
            <tbody>
              {scoring.ledger.map((entry) => (
                <tr key={entry.id} className="border-t border-slate-200 align-top">
                  <td className="py-1.5 pr-3 whitespace-nowrap">
                    {formatDateTime(entry.created_at)}
                  </td>
                  <td className="py-1.5 pr-3">{roleLabel(entry.role)}</td>
                  <td className="py-1.5 pr-3">{entry.kind}</td>
                  <td className="py-1.5 pr-3 text-right font-semibold tabular-nums">
                    {entry.points > 0 ? `+${entry.points}` : entry.points}
                  </td>
                  <td className="py-1.5">
                    {entry.reason_label}
                    {entry.provisional && <span className="text-slate-600"> · provisional</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ReviewCard>
  )
}

function pointsText(points: number | null): string {
  return points === null ? 'Back to the rules' : String(points)
}

function OverridesHistory({
  scoring,
  reasons,
}: {
  scoring: AdminDeedScoring
  reasons: { id: string; label: string }[] | undefined
}) {
  const label = (code: string) => reasons?.find((r) => r.id === code)?.label ?? code
  return (
    <ReviewCard title="Overrides">
      {scoring.overrides.length === 0 ? (
        <p className="text-slate-600">No overrides yet.</p>
      ) : (
        <ol className="flex flex-col gap-2.5">
          {scoring.overrides.map((o) => (
            <li key={o.id} className="border-t border-slate-200 pt-2 first:border-0 first:pt-0">
              <p className="font-semibold">
                {roleLabel(o.role)}: {o.previous_points} → {pointsText(o.points)}
              </p>
              <p className="text-sm text-slate-600">
                {label(o.reason_code)} · by {personName(o.actor)} · {formatDateTime(o.created_at)}
                {o.request_id ? ' · approved by a second admin' : ''}
              </p>
              <p className="text-sm break-words text-slate-800">{o.note}</p>
              {o.case_id && (
                <p className="text-[13px] break-all text-slate-600">Case {o.case_id}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </ReviewCard>
  )
}

type Mode = 'points' | 'rules'

function OverrideForm({ deedId, options }: { deedId: string; options: FraudOptionsForScoring }) {
  const queryClient = useQueryClient()
  const ids = {
    role: useId(),
    points: useId(),
    reason: useId(),
    note: useId(),
    count: useId(),
    caseId: useId(),
  }
  const [role, setRole] = useState<ScoringRole>('doer')
  const [mode, setMode] = useState<Mode>('points')
  const [points, setPoints] = useState('')
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [caseId, setCaseId] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const limits = { min: options.note_min_chars, max: options.override_max_points }

  const mutation = useMutation({
    mutationFn: () =>
      scoringAdminApi.override(deedId, {
        role,
        points: mode === 'rules' ? null : Number(points),
        reason_code: reason,
        note: note.trim(),
        case_id: caseId.trim() || null,
      }),
    onSuccess: (result) => {
      if (result.kind === 'applied') {
        queryClient.setQueryData(scoringAdminKeys.deed(deedId), result.deed)
        setDone('Points changed.')
      } else {
        void queryClient.invalidateQueries({ queryKey: scoringAdminKeys.allRequests })
        setDone(secondAdminSent(options.override_second_admin.max_cut_points))
      }
      setPoints('')
      setNote('')
      setCaseId('')
      setMode('points')
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setDone(null)
    mutation.reset()
    if (mode === 'points') {
      const value = Number(points)
      if (points.trim() === '' || !Number.isInteger(value) || value < 0 || value > limits.max) {
        setProblem(`Points must be between 0 and ${limits.max}.`)
        return
      }
    }
    if (!reason) {
      setProblem('Choose a reason.')
      return
    }
    if (note.trim().length < limits.min) {
      setProblem(`Add a note of at least ${limits.min} characters.`)
      return
    }
    setProblem(null)
    mutation.mutate()
  }

  const error = problem ?? (mutation.isError ? overrideError(mutation.error, limits) : null)
  return (
    <ReviewCard title="Change the points">
      <form onSubmit={submit} noValidate className="flex flex-col gap-3.5">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={ids.role} className="text-[15px] font-semibold">
            Whose points
          </label>
          <select
            id={ids.role}
            value={role}
            onChange={(e) => setRole(e.target.value as ScoringRole)}
            className={`${fieldClass} min-h-11`}
          >
            <option value="doer">Doer</option>
            <option value="reporter">Reporter</option>
          </select>
        </div>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-[15px] font-semibold">New points</legend>
          <label className="flex min-h-11 items-center gap-2.5">
            <input
              type="radio"
              name={`${ids.points}-mode`}
              checked={mode === 'points'}
              onChange={() => setMode('points')}
              className={`size-5 ${focusRing}`}
            />
            Set the points
          </label>
          {mode === 'points' && (
            <div className="flex flex-col gap-1 pl-7.5">
              <label htmlFor={ids.points} className="text-sm text-slate-700">
                Points (0 to {limits.max})
              </label>
              <input
                id={ids.points}
                type="number"
                inputMode="numeric"
                min={0}
                max={limits.max}
                step={1}
                value={points}
                onChange={(e) => setPoints(e.target.value)}
                className={`${fieldClass} max-w-[160px]`}
              />
            </div>
          )}
          <label className="flex min-h-11 items-center gap-2.5">
            <input
              type="radio"
              name={`${ids.points}-mode`}
              checked={mode === 'rules'}
              onChange={() => setMode('rules')}
              className={`size-5 ${focusRing}`}
            />
            Back to the rules
          </label>
        </fieldset>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={ids.reason} className="text-[15px] font-semibold">
            Reason
          </label>
          <select
            id={ids.reason}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className={`${fieldClass} min-h-11`}
          >
            <option value="">Choose a reason</option>
            {options.override_reason_codes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.note} className="text-[15px] font-semibold">
            Note for the record <span className="font-normal text-slate-600">(required)</span>
          </label>
          <textarea
            id={ids.note}
            rows={3}
            value={note}
            maxLength={options.note_max_chars}
            onChange={(e) => setNote(e.target.value)}
            aria-describedby={ids.count}
            className={fieldClass}
          />
          <CharCount id={ids.count} value={note} max={options.note_max_chars} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={ids.caseId} className="text-[15px] font-semibold">
            Case ID <span className="font-normal text-slate-600">(optional)</span>
          </label>
          <input
            id={ids.caseId}
            type="text"
            value={caseId}
            onChange={(e) => setCaseId(e.target.value)}
            autoComplete="off"
            className={fieldClass}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm font-medium text-red-700">
            {error}
          </p>
        )}
        {done && (
          <p role="status" className="text-sm font-medium text-emerald-800">
            {done}
          </p>
        )}
        <button
          type="submit"
          disabled={mutation.isPending}
          className={`${primaryButton} self-start`}
        >
          {mutation.isPending ? 'Saving…' : 'Save the change'}
        </button>
      </form>
    </ReviewCard>
  )
}
