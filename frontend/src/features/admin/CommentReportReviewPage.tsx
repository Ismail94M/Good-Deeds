import { Avatar } from '../media/avatar/Avatar'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router'
import { Loading } from '../../components/ui'
import { ApiError } from '../../lib/api'
import { useDeedOptions } from '../deeds/deedQueries'
import { categoryLabel } from '../deeds/deedText'
import { useMediaOptions } from '../media/mediaQueries'
import { firstName } from '../report/format'
import {
  adminApi,
  adminKeys,
  type CommentDecisionRequest,
  type CommentReportDetail,
} from './adminApi'
import { AdminDialog, DialogActions, NOTE_MIN, RestoreDialog } from './AdminDialog'
import { decisionError } from './decisionError'
import { formatDateTime, formatLongDate } from './adminFormat'
import { AdminMain, AdminTitle } from './adminUi'
import { useCommentReasons } from './reasonLabels'
import {
  BackToQueue,
  DecideSection,
  DecisionHistory,
  DEFAULT_NOTE_MAX,
  DoneBanner,
  ReportsCard,
  RestoreButton,
  ReviewCard,
  ReviewLoadError,
} from './reviewParts'

const QUEUE = '/admin/comment-reports'

/**
 * `/admin/comment-reports/:caseId` (Q38, _interim_ layout reusing the photo review, M3-DESIGN
 * §6.3): the full comment and its author, how many of their comments were removed in the last 90
 * days, the deed, the reports, and Decide (required note, Keep or Remove). After a decision: the
 * done banner, with Restore while the text is kept ("Can be restored until {date}.").
 */
export function CommentReportReviewPage() {
  const { caseId = '' } = useParams()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: adminKeys.commentReport(caseId),
    queryFn: () => adminApi.commentReport(caseId),
    retry: false,
  })

  const onSaved = (detail: CommentReportDetail) => {
    queryClient.setQueryData(adminKeys.commentReport(caseId), detail)
    void queryClient.invalidateQueries({ queryKey: ['admin', 'comment-reports'] })
  }

  if (query.isPending) return <Loading label="Loading this report…" />
  if (query.isError) {
    return (
      <AdminMain>
        <ReviewLoadError
          error={query.error}
          backTo={QUEUE}
          backLabel="Comment reports"
          onRetry={() => void query.refetch()}
        />
      </AdminMain>
    )
  }
  return <Review detail={query.data} onSaved={onSaved} onConflict={() => void query.refetch()} />
}

function Review({
  detail,
  onSaved,
  onConflict,
}: {
  detail: CommentReportDetail
  onSaved: (detail: CommentReportDetail) => void
  onConflict: () => void
}) {
  const { case: kase, comment, deed, reports, decisions } = detail
  const reasons = useCommentReasons()
  const categories = useDeedOptions().data?.categories
  const noteMax = useMediaOptions().data?.decision_note_max ?? DEFAULT_NOTE_MAX
  const [note, setNote] = useState('')
  const [noteTooShort, setNoteTooShort] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [openedAt] = useState(() => Date.now())
  const author = comment.author.display_name
  const authorFirst = firstName(author)

  const decide = useMutation({
    mutationFn: (data: CommentDecisionRequest) => adminApi.decideCommentReport(kase.case_id, data),
    onSuccess: (next) => {
      setConfirmRemove(false)
      setNote('')
      onSaved(next)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'case_not_open') {
        setConfirmRemove(false)
        onConflict()
      }
    },
  })
  const restore = useMutation({
    mutationFn: (restoreNote: string) => adminApi.restoreComment(comment.id, { note: restoreNote }),
    onSuccess: (next) => {
      setRestoring(false)
      onSaved(next)
    },
  })

  const noteOk = () => {
    const ok = note.trim().length >= NOTE_MIN
    setNoteTooShort(!ok)
    return ok
  }

  const open = kase.status === 'open'
  const restored = kase.decision === 'removed' && comment.state === 'visible'
  const status =
    comment.state === 'removed'
      ? { label: 'Removed', className: 'bg-red-100 text-red-800' }
      : comment.state === 'deleted'
        ? { label: 'Deleted from the deed', className: 'bg-slate-200 text-slate-700' }
        : open
          ? { label: 'Visible · report open', className: 'bg-slate-100 text-slate-700' }
          : restored
            ? { label: 'Restored', className: 'bg-emerald-50 text-emerald-800' }
            : { label: 'Kept after review', className: 'bg-emerald-50 text-emerald-800' }
  const canRestore =
    comment.state === 'removed' &&
    comment.restorable_until !== null &&
    Date.parse(comment.restorable_until) > openedAt
  const removedCount = detail.author_removed_last_90_days

  let doneTitle: string
  let doneBody: string
  if (kase.status === 'closed') {
    doneTitle = 'Report closed.'
    doneBody =
      'The comment was deleted by its author or by the people in the deed, so there’s nothing to decide.'
  } else if (restored) {
    doneTitle = 'Comment restored.'
    doneBody = `It’s back on the deed. Your note is in the audit log.`
  } else if (kase.decision === 'kept') {
    doneTitle = 'Comment kept.'
    doneBody = 'It stays up. Your note and decision are in the audit log.'
  } else {
    doneTitle = 'Comment removed.'
    doneBody = `${authorFirst} has been told it broke our community guidelines. Your note and decision are in the audit log.`
  }

  return (
    <AdminMain>
      <BackToQueue to={QUEUE} label="Comment reports" />
      {!open && (
        <DoneBanner
          title={doneTitle}
          body={doneBody}
          action={
            canRestore && (
              <RestoreButton
                label="Restore comment"
                onClick={() => {
                  restore.reset()
                  setRestoring(true)
                }}
              />
            )
          }
          footnote={
            canRestore && comment.restorable_until
              ? `Can be restored until ${formatLongDate(comment.restorable_until)}.`
              : comment.state === 'removed'
                ? 'The text has been erased, so it can’t be restored.'
                : null
          }
        />
      )}
      <AdminTitle>Review comment report</AdminTitle>
      <div className="flex flex-wrap items-start gap-6">
        <div className="flex min-w-0 flex-[1_1_420px] flex-col gap-3.5">
          <ReviewCard title="Comment">
            <span
              className={`self-start rounded-full px-2.5 py-0.5 text-[13px] font-bold ${status.className}`}
            >
              {status.label}
            </span>
            {comment.body !== null ? (
              <blockquote className="border-l-4 border-slate-300 pl-3 text-[17px] break-words whitespace-pre-line text-slate-900">
                {comment.body}
              </blockquote>
            ) : (
              <p className="text-slate-600 italic">The text has been erased.</p>
            )}
            <p className="flex items-center gap-2 text-sm text-slate-600">
              <Avatar user={comment.author} size="xs" />
              <span>
                By {author} (@{comment.author.handle}) · {formatDateTime(comment.created_at)}
              </span>
            </p>
            <p className="text-sm font-medium text-slate-800">
              {author} has had {removedCount === 1 ? '1 comment' : `${removedCount} comments`}{' '}
              removed in the last 90 days.
            </p>
          </ReviewCard>
          <ReviewCard title="Deed">
            <p>
              <strong>{deed.doer.display_name}</strong>, recognised by{' '}
              <strong>{deed.reporter.display_name}</strong>
            </p>
            <p className="text-sm text-slate-600">
              {categoryLabel(categories, deed.category)} · {deed.area_name} ·{' '}
              {deed.listed ? 'Shown in feed' : 'Hidden from feed'}
            </p>
          </ReviewCard>
        </div>
        <div className="flex min-w-0 flex-[1_1_380px] flex-col gap-3.5">
          <ReportsCard
            kind="comment"
            reports={reports.map((report) => ({
              priority: reasons.priority(report.reason),
              reason: reasons.label(report.reason),
              details: report.details,
              who: `Member @${report.reporter.handle}`,
              createdAt: report.created_at,
            }))}
          />
          <DecisionHistory decisions={decisions} />
          {open && (
            <DecideSection
              note={note}
              onNote={setNote}
              noteMax={noteMax}
              noteTooShort={noteTooShort}
              error={decide.isError && !confirmRemove ? decisionError(decide.error) : null}
              help={`If you remove it, ${authorFirst} is told it broke our community guidelines, but not who reported it. The deed and its points never change.`}
              keepLabel="Keep comment"
              removeLabel="Remove comment"
              busy={decide.isPending}
              onKeep={() => {
                if (noteOk()) decide.mutate({ action: 'keep', note: note.trim() })
              }}
              onRemove={() => {
                if (!noteOk()) return
                decide.reset()
                setConfirmRemove(true)
              }}
            />
          )}
        </div>
      </div>
      {confirmRemove && (
        <AdminDialog
          title="Remove this comment?"
          busy={decide.isPending}
          onDismiss={() => setConfirmRemove(false)}
        >
          {({ bodyId }) => (
            <>
              <p id={bodyId} className="text-slate-700">
                It disappears for everyone. {authorFirst} is told it was removed, but not who
                reported it. You can restore it later.
              </p>
              {decide.isError && (
                <p role="alert" className="text-sm font-medium text-red-700">
                  {decisionError(decide.error)}
                </p>
              )}
              <DialogActions
                confirm={decide.isPending ? 'Removing…' : 'Remove comment'}
                danger
                busy={decide.isPending}
                onConfirm={() => decide.mutate({ action: 'remove', note: note.trim() })}
                onCancel={() => setConfirmRemove(false)}
              />
            </>
          )}
        </AdminDialog>
      )}
      {restoring && (
        <RestoreDialog
          title="Restore this comment?"
          body="It comes back on the deed for everyone, and drops out of the author’s removal notices."
          confirmLabel="Restore comment"
          noteMax={noteMax}
          busy={restore.isPending}
          error={restore.isError ? decisionError(restore.error) : null}
          onCancel={() => setRestoring(false)}
          onConfirm={(restoreNote) => restore.mutate(restoreNote)}
        />
      )}
    </AdminMain>
  )
}
