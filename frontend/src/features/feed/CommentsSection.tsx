import { useId, useState, type FormEvent } from 'react'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ErrorAlert } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import type { CommentOut, PostDetail } from './feedApi'
import { useAddComment, useComments, useDeleteComment, useFeedOptions } from './feedQueries'
import { COMMENT_ERRORS, firstName, timeAgo } from './feedText'
import { CommentReportSheet } from './CommentReportSheet'
import { Avatar, DotsIcon, focusRing, secondaryButton } from './feedUi'
import { useOnboardingGate } from './OnboardingPrompt'

function commentError(error: unknown, max: number | undefined): string {
  if (error instanceof ApiError && error.code) {
    if (COMMENT_ERRORS[error.code]) return COMMENT_ERRORS[error.code]
    if (error.code === 'comment_too_long' && max) {
      return `Please keep your comment to ${max} characters.`
    }
  }
  return errorMessage(error)
}

/**
 * Comments on the deed page (F7 §2): oldest first, Delete where the API allows it (the author,
 * and the deed's doer and reporter on every comment), a composer with the kind-words, link, phone
 * and rate-limit errors inline, a confirm before deleting, and "⋯" → Report comment on other
 * people's comments (Q38; doer and reporter too, alongside Delete).
 */
export function CommentsSection({ deed, composerId }: { deed: PostDetail; composerId: string }) {
  const { user } = useAuth()
  const comments = useComments(deed.deed_id)
  const options = useFeedOptions()
  const add = useAddComment(deed.deed_id)
  const remove = useDeleteComment(deed.deed_id)
  const gate = useOnboardingGate()
  const headingId = useId()
  const helpId = useId()
  const errorId = useId()
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [toDelete, setToDelete] = useState<CommentOut | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [reporting, setReporting] = useState<string | null>(null)

  const max = options.data?.comment_max_chars
  const items = comments.data?.pages.flatMap((page) => page.items) ?? []
  const doerFirst = firstName(deed.doer.display_name)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const body = draft.trim()
    if (!body || add.isPending) return
    gate.guard(() => {
      setError(null)
      setStatus(null)
      add.mutate(body, {
        onSuccess: () => {
          setDraft('')
          setStatus('Comment posted.')
        },
        onError: (err) => {
          if (!gate.handleError(err)) setError(commentError(err, max))
        },
      })
    })
  }

  const confirmDelete = () => {
    if (!toDelete) return
    remove.mutate(toDelete.id, {
      onSuccess: () => {
        setToDelete(null)
        setDeleteError(null)
        setStatus('Comment deleted.')
      },
      onError: (err) => setDeleteError(errorMessage(err)),
    })
  }

  const deleteBody = (comment: CommentOut) => {
    if (comment.mine) return 'It’s removed for everyone.'
    if (deed.my_role === 'reporter') {
      return 'It’s removed for everyone. As the reporter of this deed, you can delete any comment on it.'
    }
    if (deed.my_role === 'doer') {
      return 'It’s removed for everyone. As the person this deed is about, you can delete any comment on it.'
    }
    return 'It’s removed for everyone.'
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-3.5 rounded-2xl border border-slate-200 bg-white p-4"
    >
      <h2 id={headingId} className="text-[17px] font-bold text-slate-900">
        Comments ({deed.comment_count})
      </h2>

      {comments.isPending ? (
        <p role="status" className="text-sm text-slate-600">
          Loading comments…
        </p>
      ) : comments.isError && items.length === 0 ? (
        <div className="flex flex-col items-start gap-2">
          <ErrorAlert>{errorMessage(comments.error)}</ErrorAlert>
          <button type="button" className={secondaryButton} onClick={() => void comments.refetch()}>
            Try again
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="text-[15px] text-slate-600">
          No comments yet. Be the first to say something kind.
        </p>
      ) : (
        <ul className="flex flex-col gap-3.5">
          {items.map((comment) => (
            <li key={comment.id} className="flex items-start gap-2.5">
              <Avatar name={comment.author.display_name} handle={comment.author.handle} size="sm" />
              <div className="min-w-0 grow">
                <p className="text-sm">
                  <strong>
                    {comment.author.display_name}
                    {comment.mine && ' (you)'}
                  </strong>{' '}
                  <span className="text-slate-500">
                    · <time dateTime={comment.created_at}>{timeAgo(comment.created_at)}</time>
                  </span>
                </p>
                <p className="mt-0.5 text-[15px] break-words whitespace-pre-line text-slate-800">
                  {comment.body}
                </p>
              </div>
              {comment.can_delete && (
                <button
                  type="button"
                  aria-label={`Delete comment by ${comment.author.display_name}`}
                  onClick={() => {
                    setDeleteError(null)
                    setToDelete(comment)
                  }}
                  className={`min-h-11 shrink-0 rounded-md px-2 text-[13px] font-semibold text-red-700 hover:bg-red-50 ${focusRing}`}
                >
                  Delete
                </button>
              )}
              {!comment.mine && (
                <button
                  type="button"
                  aria-label={`Report comment by ${comment.author.display_name}`}
                  onClick={() => gate.guard(() => setReporting(comment.id))}
                  className={`flex size-11 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-50 ${focusRing}`}
                >
                  <DotsIcon />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {comments.hasNextPage && (
        <button
          type="button"
          className={`${secondaryButton} self-start`}
          disabled={comments.isFetchingNextPage}
          onClick={() => void comments.fetchNextPage()}
        >
          {comments.isFetchingNextPage ? 'Loading…' : 'Show more comments'}
        </button>
      )}

      {items.length > 0 && deed.my_role === 'reporter' && (
        <p className="text-[13px] text-slate-600">
          You see Delete on every comment because you reported this deed. {doerFirst} sees it too.
        </p>
      )}
      {items.length > 0 && deed.my_role === 'doer' && (
        <p className="text-[13px] text-slate-600">
          You see Delete on every comment because this deed is about you.
        </p>
      )}

      <form
        onSubmit={submit}
        noValidate
        className="flex flex-col gap-1.5 border-t border-slate-200 pt-3.5"
      >
        <div className="flex items-end gap-2">
          {user && <Avatar name={user.display_name} handle={user.handle} size="sm" />}
          <textarea
            id={composerId}
            rows={2}
            aria-label="Add a comment"
            placeholder="Add a kind comment…"
            value={draft}
            maxLength={max}
            onChange={(event) => {
              setDraft(event.target.value)
              if (error) setError(null)
            }}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${errorId} ${helpId}` : helpId}
            className={`min-h-12 min-w-0 grow resize-y rounded-[10px] border bg-white px-3 py-2.5 text-[15px] leading-snug text-slate-900 ${focusRing} ${
              error ? 'border-2 border-red-600' : 'border-slate-200'
            }`}
          />
          <button
            type="submit"
            disabled={!draft.trim() || add.isPending}
            className={`min-h-12 shrink-0 rounded-[10px] bg-brand-700 px-4 text-[15px] font-semibold text-white hover:bg-brand-900 disabled:cursor-not-allowed disabled:opacity-60 ${focusRing}`}
          >
            {add.isPending ? 'Posting…' : 'Post'}
          </button>
        </div>
        {error && (
          <p id={errorId} role="alert" className="ml-11 text-sm font-semibold text-red-700">
            {error}
          </p>
        )}
        <div className="ml-11 flex justify-between gap-2 text-[13px] text-slate-600">
          <span id={helpId}>Be kind. Comments are public.</span>
          {max !== undefined && (
            <span className="font-semibold">
              {draft.length} / {max}
            </span>
          )}
        </div>
      </form>
      {status && (
        <p role="status" className="sr-only">
          {status}
        </p>
      )}

      {toDelete && (
        <ConfirmDialog
          title="Delete this comment?"
          body={deleteBody(toDelete)}
          busy={remove.isPending}
          error={deleteError}
          onDismiss={() => setToDelete(null)}
          actions={[
            { label: 'Delete comment', variant: 'danger', onClick: confirmDelete },
            {
              label: 'Cancel',
              variant: 'secondary',
              autoFocus: true,
              onClick: () => setToDelete(null),
            },
          ]}
        />
      )}
      {reporting && (
        <CommentReportSheet
          commentId={reporting}
          onClose={() => setReporting(null)}
          onOnboardingRequired={gate.handleError}
        />
      )}
      {gate.prompt}
    </section>
  )
}
