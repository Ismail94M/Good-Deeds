import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { formatLongDate } from '../admin/adminFormat'
import { BackLink, F6Main, F6Title, focusRing, secondaryButtonClass } from '../deeds/deedUi'
import {
  reviewsApi,
  reviewsKeys,
  useMyReviews,
  useUpdateReviews,
  type MyDeedRef,
  type MyExplanation,
  type MyNotice,
  type MyReviews,
  type ReviewLimits,
} from './reviewsApi'

const primaryButton = `inline-flex min-h-12 items-center justify-center rounded-lg bg-brand-700 px-5 text-base font-semibold text-white hover:bg-brand-900 disabled:opacity-60 ${focusRing}`
const fieldClass = `w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-base text-slate-900 ${focusRing}`

const EXPLANATION_INTRO =
  'We’re taking a closer look at some of your recent deeds. Most of the time everything is fine. If you’d like, tell us about them.'

const dayFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})

/** `YYYY-MM-DD` → "26 November 2026" (a calendar day, no time zone shift). */
function formatDay(day: string): string {
  const date = new Date(`${day}T12:00:00Z`)
  return Number.isNaN(date.getTime()) ? day : dayFormat.format(date)
}

/**
 * `/me/reviews` (M5 F25, §6.2 "Reviews (member)", D99–D101): explanation requests and decision
 * notices about the person's own deeds. Reason, effect and outcome texts come from the server as
 * they are. Opening the page marks everything seen (`POST /reviews/seen`), which clears the
 * Profile dot. Nothing here says "fraud" or names anyone but the other person on each deed.
 */
export function ReviewsPage() {
  const reviews = useMyReviews()
  const queryClient = useQueryClient()
  const { hash } = useLocation()
  const seenSent = useRef(false)
  const loaded = reviews.isSuccess

  useEffect(() => {
    if (!loaded || seenSent.current) return
    seenSent.current = true
    reviewsApi
      .seen()
      .then(() =>
        queryClient.setQueryData<MyReviews>(reviewsKeys.all, (current) =>
          current ? { ...current, unread: 0 } : current,
        ),
      )
      .catch(() => {
        // Not worth an error: the dot stays and the next visit tries again.
      })
  }, [loaded, queryClient])

  useEffect(() => {
    if (!loaded || !hash) return
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.()
  }, [loaded, hash])

  return (
    <F6Main>
      <BackLink to="/profile" label="Profile" />
      <F6Title>Reviews</F6Title>
      {reviews.isPending ? (
        <Loading label="Loading your reviews…" />
      ) : reviews.isError ? (
        <div className="flex flex-col items-start gap-3">
          <ErrorAlert>{errorMessage(reviews.error)}</ErrorAlert>
          <button
            type="button"
            onClick={() => void reviews.refetch()}
            className={secondaryButtonClass}
          >
            Try again
          </button>
        </div>
      ) : reviews.data.explanations.length === 0 && reviews.data.notices.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-10 text-center">
          <h2 className="text-lg font-bold text-slate-900">Nothing to review</h2>
          <p className="text-slate-600">
            If we ever need to ask you about your deeds, you’ll see it here.
          </p>
        </div>
      ) : (
        <ul className="flex flex-col gap-4">
          {reviews.data.explanations.map((item) => (
            <li key={`e-${item.id}`}>
              <ExplanationCard item={item} limits={reviews.data.limits} />
            </li>
          ))}
          {reviews.data.notices.map((notice) => (
            <li key={`n-${notice.id}`}>
              <NoticeCard notice={notice} limits={reviews.data.limits} />
            </li>
          ))}
        </ul>
      )}
    </F6Main>
  )
}

function DeedList({ deeds }: { deeds: { deed: MyDeedRef; effect?: string }[] }) {
  return (
    <ul className="flex flex-col divide-y divide-slate-200 rounded-xl border border-slate-200">
      {deeds.map(({ deed, effect }) => (
        <li key={deed.deed_id} className="flex flex-col gap-0.5 px-3.5 py-2.5">
          <span className="font-semibold break-words text-slate-900">
            {deed.role === 'doer'
              ? `${deed.other_person.display_name} recognised you`
              : `You recognised ${deed.other_person.display_name}`}
          </span>
          <span className="text-sm break-words text-slate-600">
            {deed.category_label} · {formatDay(deed.occurred_on)} · {deed.area_name}
          </span>
          {effect && <span className="text-sm font-semibold text-brand-900">{effect}</span>}
        </li>
      ))}
    </ul>
  )
}

function Card({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  const headingId = useId()
  return (
    <article
      id={id}
      aria-labelledby={headingId}
      className="flex scroll-mt-24 flex-col gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-4"
    >
      <h2 id={headingId} className="text-[17px] font-bold text-slate-900">
        {title}
      </h2>
      {children}
    </article>
  )
}

function replyError(error: unknown, limits: ReviewLimits): string {
  const code = error instanceof ApiError ? error.code : null
  if (code === 'reply_closed') return 'The time to reply has passed.'
  if (code === 'already_replied') return 'We already have your reply.'
  if (code === 'reply_too_long')
    return `Your reply can be up to ${limits.reply_max_chars} characters.`
  if (error instanceof ApiError && error.status === 404)
    return 'This request isn’t available any more.'
  return errorMessage(error)
}

function ExplanationCard({ item, limits }: { item: MyExplanation; limits: ReviewLimits }) {
  const update = useUpdateReviews()
  const queryClient = useQueryClient()
  const replyId = useId()
  const countId = useId()
  const dueId = useId()
  const [reply, setReply] = useState('')
  const mutation = useMutation({
    mutationFn: () => reviewsApi.reply(item.id, reply.trim()),
    onSuccess: (saved) =>
      update((current) => ({
        ...current,
        explanations: current.explanations.map((e) => (e.id === saved.id ? saved : e)),
      })),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: reviewsKeys.all })
      }
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!reply.trim()) return
    mutation.mutate()
  }

  return (
    <Card title="About some of your deeds">
      <p className="text-slate-700">{EXPLANATION_INTRO}</p>
      <DeedList deeds={item.deeds.map((deed) => ({ deed }))} />
      {item.status === 'waiting' && (
        <form onSubmit={submit} className="flex flex-col gap-2" noValidate>
          <label htmlFor={replyId} className="text-[15px] font-semibold">
            Your reply <span className="font-normal text-slate-600">(optional)</span>
          </label>
          <textarea
            id={replyId}
            rows={4}
            value={reply}
            maxLength={limits.reply_max_chars}
            onChange={(e) => setReply(e.target.value)}
            aria-describedby={`${dueId} ${countId}`}
            className={fieldClass}
          />
          <div className="flex flex-wrap justify-between gap-2 text-[13px] text-slate-600">
            <span id={dueId}>Reply by {formatLongDate(item.reply_due_at)}</span>
            <span id={countId} aria-live="polite">
              {reply.length} / {limits.reply_max_chars}
            </span>
          </div>
          {mutation.isError && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {replyError(mutation.error, limits)}
            </p>
          )}
          <button
            type="submit"
            disabled={mutation.isPending || !reply.trim()}
            className={`${primaryButton} self-start`}
          >
            {mutation.isPending ? 'Sending…' : 'Send'}
          </button>
        </form>
      )}
      {item.reply !== null && (
        <>
          <p role="status" className="font-medium text-emerald-800">
            Thanks, we’ve got your reply.
          </p>
          <blockquote className="border-l-4 border-slate-200 pl-3 text-[15px] break-words whitespace-pre-line text-slate-700">
            {item.reply}
          </blockquote>
        </>
      )}
      {item.outcome_text && <p className="font-medium text-slate-900">{item.outcome_text}</p>}
    </Card>
  )
}

function appealError(error: unknown, limits: ReviewLimits): string {
  const code = error instanceof ApiError ? error.code : null
  if (code === 'appeal_closed') return 'The time to ask for another review has passed.'
  if (code === 'already_appealed') return 'You’ve already asked for another review.'
  if (code === 'reason_required') {
    return `Tell us why in at least ${limits.appeal_reason_min_chars} characters.`
  }
  if (code === 'reason_too_long') {
    return `Your reason can be up to ${limits.appeal_reason_max_chars} characters.`
  }
  if (error instanceof ApiError && error.status === 404)
    return 'This decision isn’t available any more.'
  return errorMessage(error)
}

function NoticeCard({ notice, limits }: { notice: MyNotice; limits: ReviewLimits }) {
  return (
    <Card id={`notice-${notice.id}`} title="A decision about your deeds">
      <p className="text-slate-700">{notice.reason_text}</p>
      <DeedList deeds={notice.items.map((i) => ({ deed: i.deed, effect: i.effect_text }))} />
      {notice.appeal ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-sm text-slate-600">
            You asked for another review on {formatLongDate(notice.appeal.created_at)}.
          </p>
          <blockquote className="border-l-4 border-slate-200 pl-3 text-[15px] break-words whitespace-pre-line text-slate-700">
            {notice.appeal.reason}
          </blockquote>
          {notice.appeal.outcome_text ? (
            <p className="font-medium text-slate-900">{notice.appeal.outcome_text}</p>
          ) : (
            <p className="text-slate-700">We’ll show the answer here.</p>
          )}
        </div>
      ) : (
        notice.can_appeal && <AppealForm notice={notice} limits={limits} />
      )}
    </Card>
  )
}

function AppealForm({ notice, limits }: { notice: MyNotice; limits: ReviewLimits }) {
  const update = useUpdateReviews()
  const queryClient = useQueryClient()
  const reasonId = useId()
  const countId = useId()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [tooShort, setTooShort] = useState(false)
  const mutation = useMutation({
    mutationFn: () => reviewsApi.appeal(notice.id, reason.trim()),
    onSuccess: (saved) =>
      update((current) => ({
        ...current,
        notices: current.notices.map((n) => (n.id === saved.id ? saved : n)),
      })),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: reviewsKeys.all })
      }
    },
  })
  const until = formatLongDate(notice.appeal_until)

  if (!open) {
    return (
      <div className="flex flex-col items-start gap-1.5">
        <button type="button" onClick={() => setOpen(true)} className={secondaryButtonClass}>
          Ask for another review
        </button>
        <p className="text-sm text-slate-600">You can ask until {until}.</p>
      </div>
    )
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (reason.trim().length < limits.appeal_reason_min_chars) {
      setTooShort(true)
      return
    }
    setTooShort(false)
    mutation.mutate()
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2" noValidate>
      <label htmlFor={reasonId} className="text-[15px] font-semibold">
        Why should we look again?
      </label>
      <textarea
        id={reasonId}
        rows={4}
        value={reason}
        autoFocus
        maxLength={limits.appeal_reason_max_chars}
        onChange={(e) => setReason(e.target.value)}
        aria-describedby={countId}
        aria-invalid={tooShort || undefined}
        className={fieldClass}
      />
      <div className="flex flex-wrap justify-between gap-2 text-[13px] text-slate-600">
        <span>You can ask until {until}.</span>
        <span id={countId} aria-live="polite">
          {reason.length} / {limits.appeal_reason_max_chars}
        </span>
      </div>
      {(tooShort || mutation.isError) && (
        <p role="alert" className="text-sm font-medium text-red-700">
          {tooShort
            ? `Tell us why in at least ${limits.appeal_reason_min_chars} characters.`
            : appealError(mutation.error, limits)}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={mutation.isPending} className={primaryButton}>
          {mutation.isPending ? 'Sending…' : 'Ask for another review'}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          disabled={mutation.isPending}
          className={secondaryButtonClass}
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
