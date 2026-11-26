import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError } from '../../lib/api'
import { NotFoundPage } from '../../app/NotFoundPage'
import { fieldClass } from './AdminDialog'
import { formatDateTime } from './adminFormat'
import { AdminMain, AdminTitle, focusRing } from './adminUi'
import {
  caseTypeLabel,
  fraudApi,
  fraudKeys,
  useFraudOptions,
  type CaseDeed,
  type CaseDetail,
  type CaseEdge,
  type DeedAction,
  type DecisionRequest,
  type FraudOptions,
} from './fraudApi'
import {
  RELOAD_CODES,
  formatMonth,
  formatShortDate,
  fraudError,
  isUrgent,
  useNote,
} from './fraudFormat'
import {
  Chip,
  DialogButtons,
  FraudDialog,
  linkClass,
  NoteDialog,
  NoteField,
  secondaryButtonClass,
} from './fraudParts'
import { BackToQueue, DoneBanner, ReviewCard } from './reviewParts'

const QUEUE = '/admin/fraud'

const ACTION_LABELS: Record<string, string> = {
  park: 'Parked',
  unpark: 'Moved back to Open',
  request_explanation: 'Asked for an explanation',
  confirm: 'Confirmed',
  dismiss: 'Dismissed',
}

const OUTCOME_LABELS: Record<DeedAction, string> = {
  release: 'Released',
  exclude: 'Excluded from leaderboards',
  reject: 'Rejected',
}

/** The per-deed choices in the Confirm dialog (§6.2); Release is the default (D88). */
const DEED_CHOICES: { id: DeedAction; label: string }[] = [
  { id: 'release', label: 'Release' },
  { id: 'exclude', label: 'Keep but exclude from leaderboards' },
  { id: 'reject', label: 'Reject the deed' },
]

const STATUS_WORDS: Record<string, string> = {
  verified: 'Verified',
  pending: 'Pending',
  withdrawn: 'Withdrawn',
  rejected: 'Rejected',
  expired: 'Expired',
}

const deedHref = (id: string) => `/admin/scoring/deeds/${encodeURIComponent(id)}`
const userHref = (handle: string) => `/admin/users/${encodeURIComponent(handle)}`

type Dialog = 'park' | 'unpark' | 'ask' | 'dismiss' | 'confirm' | null

/**
 * `/admin/fraud/:caseId` (F21, F25, M5-DESIGN §6.2): what was found, the people, connections,
 * deeds, decision history and explanations; Park / Unpark, Ask for an explanation, Dismiss and
 * Confirm (gated by D99). A conflicted admin gets 404 and sees the NotFound page (D90).
 */
export function FraudCaseReviewPage() {
  const { caseId = '' } = useParams()
  const options = useFraudOptions().data
  const query = useQuery({
    queryKey: fraudKeys.case(caseId),
    queryFn: () => fraudApi.case(caseId),
    retry: false,
  })

  if (query.isPending) return <Loading label="Loading this case…" />
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundPage />
    return (
      <AdminMain>
        <BackToQueue to={QUEUE} label="Fraud cases" />
        <ErrorAlert>{fraudError(query.error, options)}</ErrorAlert>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className={`${secondaryButtonClass} self-start`}
        >
          Try again
        </button>
      </AdminMain>
    )
  }
  return (
    <Review
      key={caseId}
      detail={query.data}
      options={options}
      onReload={() => void query.refetch()}
    />
  )
}

function Review({
  detail,
  options,
  onReload,
}: {
  detail: CaseDetail
  options: FraudOptions | undefined
  onReload: () => void
}) {
  const queryClient = useQueryClient()
  const kase = detail.case
  const caseId = kase.case_id
  const [dialog, setDialog] = useState<Dialog>(null)
  const [now] = useState(() => Date.now())

  const action = useMutation({
    mutationFn: (run: () => Promise<CaseDetail>) => run(),
    onSuccess: (next) => {
      setDialog(null)
      queryClient.setQueryData(fraudKeys.case(caseId), next)
      void queryClient.invalidateQueries({
        queryKey: fraudKeys.all,
        predicate: (q) => q.queryKey[2] !== 'case' && q.queryKey[2] !== 'options',
      })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code && RELOAD_CODES.has(error.code)) onReload()
    },
  })
  const open = (next: Dialog) => {
    action.reset()
    setDialog(next)
  }
  const close = () => setDialog(null)
  const error = action.isError ? fraudError(action.error, options) : null

  const resolved = kase.status === 'resolved'
  const asked = new Set(detail.explanations.map((e) => e.user.handle))
  const notAsked = detail.users.filter((u) => !asked.has(u.user.handle))
  const waiting = detail.explanations.filter(
    (e) => e.replied_at === null && Date.parse(e.reply_due_at) > now,
  )
  const lastDue = waiting.reduce<string | null>(
    (latest, e) => (latest === null || e.reply_due_at > latest ? e.reply_due_at : latest),
    null,
  )
  const confirmBlocked =
    detail.explanations.length === 0
      ? 'Ask the people involved for an explanation first'
      : lastDue
        ? `Waiting for a reply until ${formatShortDate(lastDue)}`
        : null

  return (
    <AdminMain>
      <BackToQueue to={QUEUE} label="Fraud cases" />
      {resolved && (
        <DoneBanner
          title={kase.outcome === 'confirmed' ? 'Case confirmed.' : 'Case dismissed.'}
          body={
            kase.outcome === 'confirmed'
              ? 'People with an adverse outcome have been told about their own deeds and can ask for another review.'
              : 'Nothing changed for anyone.'
          }
          footnote={kase.resolved_at ? `Decided ${formatDateTime(kase.resolved_at)}.` : null}
        />
      )}
      <header className="flex flex-col gap-2">
        <AdminTitle>{caseTypeLabel(options, kase.type)} case</AdminTitle>
        <div className="flex flex-wrap items-center gap-1.5 text-sm text-slate-600">
          <Chip tone={resolved ? (kase.outcome === 'confirmed' ? 'red' : 'green') : 'grey'}>
            {resolved
              ? kase.outcome === 'confirmed'
                ? 'Resolved · Confirmed'
                : 'Resolved · Dismissed'
              : kase.status === 'parked'
                ? 'Parked'
                : 'Open'}
          </Chip>
          <span>Opened {formatDateTime(kase.opened_at)}</span>
        </div>
        {detail.finalization.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Board deadlines">
            {detail.finalization.map((m) => (
              <li key={m.period_month}>
                <Chip tone={!m.final && isUrgent(m.finalizes_at, now) ? 'red' : 'grey'}>
                  {m.final
                    ? `${formatMonth(m.period_month)} boards are final`
                    : `${formatMonth(m.period_month)} board final ${formatDateTime(m.finalizes_at)}`}
                </Chip>
              </li>
            ))}
          </ul>
        )}
      </header>

      {!resolved && (
        <Actions
          status={kase.status}
          canAsk={notAsked.length > 0}
          confirmBlocked={confirmBlocked}
          onOpen={open}
        />
      )}

      <div className="flex flex-wrap items-start gap-4">
        <div className="flex min-w-0 flex-[1_1_480px] flex-col gap-3.5">
          <ReviewCard title="What was found">
            <p className="font-semibold break-words text-slate-900">{kase.summary}</p>
            <SignalWords type={kase.type} signal={kase.signal} edges={detail.edges} />
            <p className="text-sm text-slate-600">
              Counts below cover {formatShortDate(detail.window.since)} to{' '}
              {formatShortDate(detail.window.until)}.
            </p>
          </ReviewCard>
          <DeedsCard deeds={detail.deeds} />
          <EdgesCard edges={detail.edges} />
        </div>
        <div className="flex min-w-0 flex-[1_1_340px] flex-col gap-3.5">
          <PeopleCard detail={detail} confirmed={kase.outcome === 'confirmed'} />
          <ExplanationsCard detail={detail} now={now} />
          <HistoryCard detail={detail} />
        </div>
      </div>

      {(dialog === 'park' || dialog === 'unpark') && (
        <NoteDialog
          title={dialog === 'park' ? 'Park this case?' : 'Move this case back to Open?'}
          body={
            <p>
              {dialog === 'park'
                ? 'It leaves the open queue until someone moves it back. Nothing changes for anyone.'
                : 'It goes back into the open queue. Nothing changes for anyone.'}
            </p>
          }
          confirmLabel={dialog === 'park' ? 'Park case' : 'Move to Open'}
          busyLabel="Saving…"
          options={options}
          busy={action.isPending}
          error={error}
          onCancel={close}
          onConfirm={(note) =>
            action.mutate(() =>
              dialog === 'park'
                ? fraudApi.park(caseId, { note })
                : fraudApi.unpark(caseId, { note }),
            )
          }
        />
      )}
      {dialog === 'dismiss' && (
        <NoteDialog
          title="Dismiss this case?"
          body={
            <p>
              Nothing changes for anyone. People you asked for an explanation are told “We’ve
              finished looking. No changes.” A dismissed case can’t be reopened.
            </p>
          }
          confirmLabel="Dismiss case"
          busyLabel="Dismissing…"
          options={options}
          busy={action.isPending}
          error={error}
          onCancel={close}
          onConfirm={(note) =>
            action.mutate(() => fraudApi.decide(caseId, { outcome: 'dismissed', note }))
          }
        />
      )}
      {dialog === 'ask' && (
        <AskDialog
          people={notAsked.map((u) => u.user)}
          options={options}
          busy={action.isPending}
          error={error}
          onCancel={close}
          onConfirm={(handles, note) =>
            action.mutate(() => fraudApi.askForExplanations(caseId, { handles, note }))
          }
        />
      )}
      {dialog === 'confirm' && (
        <ConfirmDialog
          deeds={detail.deeds}
          options={options}
          busy={action.isPending}
          error={error}
          onCancel={close}
          onConfirm={(data) => action.mutate(() => fraudApi.decide(caseId, data))}
        />
      )}
    </AdminMain>
  )
}

// ----------------------------------------------------------------- actions

function Actions({
  status,
  canAsk,
  confirmBlocked,
  onOpen,
}: {
  status: string
  canAsk: boolean
  confirmBlocked: string | null
  onOpen: (dialog: Dialog) => void
}) {
  const hintId = useId()
  return (
    <section
      aria-label="Decide"
      className="flex flex-col gap-2.5 rounded-xl border border-slate-300 bg-white p-3.5"
    >
      <div className="flex flex-wrap gap-2">
        {status === 'parked' ? (
          <button type="button" className={secondaryButtonClass} onClick={() => onOpen('unpark')}>
            Unpark
          </button>
        ) : (
          <button type="button" className={secondaryButtonClass} onClick={() => onOpen('park')}>
            Park
          </button>
        )}
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={!canAsk}
          onClick={() => onOpen('ask')}
        >
          Ask for an explanation
        </button>
        <button type="button" className={secondaryButtonClass} onClick={() => onOpen('dismiss')}>
          Dismiss
        </button>
        <button
          type="button"
          disabled={confirmBlocked !== null}
          aria-describedby={confirmBlocked ? hintId : undefined}
          onClick={() => onOpen('confirm')}
          className={`inline-flex min-h-11 items-center justify-center rounded-lg bg-red-700 px-4 text-[15px] font-semibold text-white hover:bg-red-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-700 ${focusRing}`}
        >
          Confirm
        </button>
      </div>
      {confirmBlocked && (
        <p id={hintId} className="text-sm text-slate-700">
          {confirmBlocked}
        </p>
      )}
      {!canAsk && <p className="text-sm text-slate-600">Everyone in this case has been asked.</p>}
    </section>
  )
}

function AskDialog({
  people,
  options,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  people: { handle: string; display_name: string }[]
  options: FraudOptions | undefined
  busy: boolean
  error: string | null
  onConfirm: (handles: string[], note: string) => void
  onCancel: () => void
}) {
  const field = useNote(options)
  const [chosen, setChosen] = useState(() => new Set(people.map((p) => p.handle)))
  const [noneChosen, setNoneChosen] = useState(false)
  const days = options?.explanation_reply_days
  return (
    <FraudDialog title="Ask for an explanation" onDismiss={onCancel} busy={busy}>
      {({ bodyId }) => (
        <>
          <p id={bodyId} className="text-slate-700">
            Each person sees a neutral request under Profile → Reviews about their own deeds in this
            case{days ? `, and can reply within ${days} days` : ''}. It never names the other people
            or says why.
          </p>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-[15px] font-semibold">Who to ask</legend>
            {people.map((p, index) => (
              <label key={p.handle} className="flex min-h-11 items-center gap-3">
                <input
                  type="checkbox"
                  className={`size-5 ${focusRing}`}
                  checked={chosen.has(p.handle)}
                  data-autofocus={index === 0 ? true : undefined}
                  onChange={(event) => {
                    const next = new Set(chosen)
                    if (event.target.checked) next.add(p.handle)
                    else next.delete(p.handle)
                    setChosen(next)
                  }}
                />
                <span>
                  {p.display_name} <span className="text-slate-600">@{p.handle}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <NoteField
            note={field.note}
            onNote={field.setNote}
            options={options}
            invalid={field.invalid}
            placeholder="Why you’re asking (only admins see this)"
          />
          {(noneChosen || field.invalid || error) && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {noneChosen
                ? 'Choose at least one person to ask.'
                : field.invalid
                  ? field.tooShortMessage
                  : error}
            </p>
          )}
          <DialogButtons
            confirm={busy ? 'Sending…' : 'Send request'}
            busy={busy}
            onCancel={onCancel}
            onConfirm={() => {
              const none = chosen.size === 0
              setNoneChosen(none)
              const text = field.check()
              if (!none && text !== null) onConfirm([...chosen], text)
            }}
          />
        </>
      )}
    </FraudDialog>
  )
}

function ConfirmDialog({
  deeds,
  options,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  deeds: CaseDeed[]
  options: FraudOptions | undefined
  busy: boolean
  error: string | null
  onConfirm: (data: DecisionRequest) => void
  onCancel: () => void
}) {
  const field = useNote(options)
  const [choices, setChoices] = useState<Record<string, DeedAction>>(() =>
    Object.fromEntries(deeds.map((d) => [d.deed_id, 'release' as DeedAction])),
  )
  const days = options?.appeal_window_days ?? 30
  return (
    <FraudDialog title="Confirm this case" onDismiss={onCancel} busy={busy} wide>
      {({ bodyId }) => (
        <>
          <p id={bodyId} className="text-slate-700">
            Choose what happens to each deed. People are told the outcome for their own deeds and
            can ask for another review within {days} days. A rejection only happens after that.
          </p>
          <ul className="flex flex-col gap-2.5">
            {deeds.map((deed, index) => (
              <li key={deed.deed_id} className="rounded-lg border border-slate-200 p-3">
                <DeedChoice
                  deed={deed}
                  value={choices[deed.deed_id] ?? 'release'}
                  autoFocus={index === 0}
                  onChange={(value) => setChoices({ ...choices, [deed.deed_id]: value })}
                />
              </li>
            ))}
          </ul>
          <NoteField
            note={field.note}
            onNote={field.setNote}
            options={options}
            invalid={field.invalid}
          />
          {(field.invalid || error) && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {field.invalid ? field.tooShortMessage : error}
            </p>
          )}
          <DialogButtons
            confirm={busy ? 'Confirming…' : 'Confirm case'}
            danger
            busy={busy}
            onCancel={onCancel}
            onConfirm={() => {
              const note = field.check()
              if (note === null) return
              onConfirm({
                outcome: 'confirmed',
                note,
                deeds: deeds.map((d) => ({
                  deed_id: d.deed_id,
                  action: choices[d.deed_id] ?? 'release',
                })),
              })
            }}
          />
        </>
      )}
    </FraudDialog>
  )
}

function DeedChoice({
  deed,
  value,
  autoFocus,
  onChange,
}: {
  deed: CaseDeed
  value: DeedAction
  autoFocus: boolean
  onChange: (value: DeedAction) => void
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="flex flex-col gap-0.5">
        <span className="font-semibold break-words">
          {deed.reporter.display_name} → {deed.doer.display_name}
        </span>
        <span className="text-sm text-slate-600">
          {deed.category_label} · {deed.area_name}
          {deed.verified_at ? ` · ${formatShortDate(deed.verified_at)}` : ''}
        </span>
      </label>
      {deed.independent_witness && (
        <span>
          <Chip tone="green">Independent witness too</Chip>
        </span>
      )}
      <select
        id={id}
        value={value}
        data-autofocus={autoFocus ? true : undefined}
        onChange={(event) => onChange(event.target.value as DeedAction)}
        className={fieldClass}
      >
        {DEED_CHOICES.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
      </select>
    </div>
  )
}

// ----------------------------------------------------------------- cards

/** The detector's numbers in words (§6.2 "What was found"). */
function SignalWords({
  type,
  signal,
  edges,
}: {
  type: string
  signal: Record<string, unknown>
  edges: CaseEdge[]
}) {
  const num = (key: string) => (typeof signal[key] === 'number' ? (signal[key] as number) : null)
  const lines: ReactNode[] = []
  if (type === 'reciprocal') {
    const a = num('a_to_b')
    const b = num('b_to_a')
    const gap = num('first_gap_hours')
    if (a !== null && b !== null) lines.push(`Recognitions each way: ${a} and ${b}.`)
    if (gap !== null) lines.push(`The first one back came ${gap} h after the first.`)
  } else if (type === 'ring') {
    const share = num('internal_share_pct')
    const path = ringPath(edges)
    if (path || share !== null) {
      lines.push(
        `${path ?? 'The group'}${share !== null ? `, ${share} % of what they received came from each other` : ''}.`,
      )
    }
  } else if (type === 'concentration') {
    const bp = num('share_bp')
    const points = num('doer_points')
    const month = typeof signal.period_month === 'string' ? signal.period_month : null
    if (bp !== null) {
      lines.push(
        `${Math.floor(bp / 100)} % of ${points ?? 'the'} leaderboard points${
          month ? ` in ${formatMonth(month)}` : ''
        } came from one reporter.`,
      )
    }
  } else if (type === 'content') {
    const check = typeof signal.text_check === 'string' ? signal.text_check : null
    if (check) {
      lines.push(
        check === 'spam_or_empty'
          ? 'The text check found the description may be spam or empty.'
          : `The text check flagged the description (${check.replace(/_/g, ' ')}).`,
      )
    }
  }
  if (lines.length === 0) {
    const entries = Object.entries(signal)
    if (entries.length === 0) return null
    return (
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-sm text-slate-700">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="font-semibold">{key.replace(/_/g, ' ')}</dt>
            <dd className="break-words">{String(value)}</dd>
          </div>
        ))}
      </dl>
    )
  }
  return (
    <>
      {lines.map((line, index) => (
        <p key={index} className="break-words text-slate-700">
          {line}
        </p>
      ))}
    </>
  )
}

/** "Ravi → Kavya → Sneha → Ravi" when the edges form one cycle, else null. */
function ringPath(edges: CaseEdge[]): string | null {
  if (edges.length < 3) return null
  const next = new Map(edges.map((e) => [e.from.handle, e.to]))
  if (next.size !== edges.length) return null
  const start = edges[0].from
  const names = [start.display_name]
  let at = start.handle
  for (let i = 0; i < edges.length; i += 1) {
    const to = next.get(at)
    if (!to) return null
    names.push(to.display_name)
    at = to.handle
  }
  return at === start.handle ? names.join(' → ') : null
}

function DeedsCard({ deeds }: { deeds: CaseDeed[] }) {
  return (
    <ReviewCard title={`Deeds (${deeds.length})`}>
      <ul className="flex flex-col">
        {deeds.map((deed, index) => {
          // Each fact is shown once: a rejected deed reads "Rejected", not "Rejected · Held · Rejected".
          const rejected = deed.status === 'rejected'
          return (
            <li
              key={deed.deed_id}
              className={`flex flex-col gap-1 ${index > 0 ? 'mt-2.5 border-t border-slate-200 pt-2.5' : ''}`}
            >
              <p className="font-semibold break-words text-slate-900">
                {deed.reporter.display_name} → {deed.doer.display_name}
              </p>
              <p className="text-sm text-slate-600">
                {deed.category_label} · {deed.area_name}
                {deed.verified_at ? ` · Verified ${formatShortDate(deed.verified_at)}` : ''}
              </p>
              {deed.description && (
                <p className="line-clamp-3 text-sm break-words text-slate-700">
                  “{deed.description}”
                </p>
              )}
              <div className="flex flex-wrap gap-1.5">
                <Chip tone={rejected ? 'red' : 'grey'}>
                  {STATUS_WORDS[deed.status] ?? deed.status}
                </Chip>
                {!deed.counts_for_points && <Chip>Not counted for points</Chip>}
                {/* Once rejected, a hold no longer matters (D80: it becomes moot). */}
                {deed.held && !rejected && <Chip tone="amber">Held</Chip>}
                {deed.independent_witness && <Chip tone="green">Independent witness too</Chip>}
                {/* "Rejected" outcome on a rejected deed would repeat the status chip. */}
                {deed.outcome && !(rejected && deed.outcome === 'reject') && (
                  <Chip tone={deed.outcome === 'release' ? 'green' : 'red'}>
                    {OUTCOME_LABELS[deed.outcome]}
                  </Chip>
                )}
              </div>
              <Link
                to={deedHref(deed.deed_id)}
                className={`${linkClass} self-start text-sm`}
                aria-label={`Points and overrides for ${deed.reporter.display_name} → ${deed.doer.display_name}, ${deed.category_label}`}
              >
                Points and overrides
              </Link>
            </li>
          )
        })}
      </ul>
    </ReviewCard>
  )
}

function EdgesCard({ edges }: { edges: CaseEdge[] }) {
  if (edges.length === 0) return null
  return (
    <ReviewCard title="Connections">
      <ul className="flex flex-col gap-2">
        {edges.map((edge) => (
          <li
            key={`${edge.from.handle}-${edge.to.handle}`}
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm"
          >
            <span className="font-semibold break-words text-slate-900">
              {edge.from.display_name} → {edge.to.display_name}
            </span>
            <span className="text-slate-600">
              {edge.deeds === 1 ? '1 deed' : `${edge.deeds} deeds`}
            </span>
            {edge.shared_device && <Chip tone="amber">Same phone used</Chip>}
            {edge.shared_network && <Chip tone="amber">Same network</Chip>}
          </li>
        ))}
      </ul>
    </ReviewCard>
  )
}

function PeopleCard({ detail, confirmed }: { detail: CaseDetail; confirmed: boolean }) {
  return (
    <ReviewCard title={`People (${detail.users.length})`}>
      <ul className="flex flex-col">
        {detail.users.map((person, index) => (
          <li
            key={person.user.handle}
            className={`flex flex-col gap-1 ${index > 0 ? 'mt-2.5 border-t border-slate-200 pt-2.5' : ''}`}
          >
            <p className="break-words">
              <span className="font-semibold text-slate-900">{person.user.display_name}</span>{' '}
              <span className="text-sm text-slate-600">@{person.user.handle}</span>
            </p>
            <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 text-sm text-slate-700">
              <dt>Joined</dt>
              <dd>{formatShortDate(person.joined_at)}</dd>
              <dt>Verified deeds as doer</dt>
              <dd>{person.verified_as_doer_in_window}</dd>
              <dt>Verified deeds as reporter</dt>
              <dd>{person.verified_as_reporter_in_window}</dd>
              <dt>Other open cases</dt>
              <dd>{person.other_open_cases}</dd>
              <dt>Earlier confirmed cases</dt>
              <dd>{person.earlier_confirmed_cases}</dd>
            </dl>
            {confirmed && (
              <Link
                to={userHref(person.user.handle)}
                className={`${linkClass} self-start text-sm`}
                aria-label={`Revoke titles or badges for ${person.user.display_name}`}
              >
                Revoke titles or badges
              </Link>
            )}
          </li>
        ))}
      </ul>
    </ReviewCard>
  )
}

function ExplanationsCard({ detail, now }: { detail: CaseDetail; now: number }) {
  return (
    <ReviewCard title="Explanations">
      {detail.explanations.length === 0 ? (
        <p className="text-sm text-slate-600">No one has been asked yet.</p>
      ) : (
        <ul className="flex flex-col">
          {detail.explanations.map((e, index) => {
            const overdue = e.replied_at === null && Date.parse(e.reply_due_at) <= now
            return (
              <li
                key={e.user.handle}
                className={`flex flex-col gap-1 ${index > 0 ? 'mt-2.5 border-t border-slate-200 pt-2.5' : ''}`}
              >
                <p className="font-semibold text-slate-900">{e.user.display_name}</p>
                <p className="text-sm text-slate-600">
                  Asked {formatShortDate(e.requested_at)} · reply due{' '}
                  {formatShortDate(e.reply_due_at)}
                </p>
                {e.replied_at !== null ? (
                  <>
                    <p className="text-sm text-slate-600">
                      Replied {formatShortDate(e.replied_at)}
                    </p>
                    {e.reply ? (
                      <blockquote className="border-l-4 border-slate-300 pl-3 break-words whitespace-pre-line text-slate-900">
                        {e.reply}
                      </blockquote>
                    ) : (
                      <p className="text-sm text-slate-600 italic">No text.</p>
                    )}
                  </>
                ) : (
                  <Chip tone={overdue ? 'grey' : 'amber'}>
                    {overdue ? 'No reply, due date passed' : 'Waiting for a reply'}
                  </Chip>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </ReviewCard>
  )
}

function HistoryCard({ detail }: { detail: CaseDetail }) {
  if (detail.decisions.length === 0) return null
  return (
    <ReviewCard title="Decision history">
      <ul className="flex flex-col gap-2">
        {detail.decisions.map((record, index) => (
          <li key={index} className="text-sm">
            <p className="font-semibold text-slate-900">
              {ACTION_LABELS[record.action] ?? record.action} by {record.actor.display_name} ·{' '}
              {formatDateTime(record.created_at)}
            </p>
            <p className="break-words whitespace-pre-line text-slate-700">{record.note}</p>
          </li>
        ))}
      </ul>
    </ReviewCard>
  )
}
