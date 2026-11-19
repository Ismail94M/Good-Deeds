import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router'
import { Loading } from '../../components/ui'
import { ApiError } from '../../lib/api'
import { useDeedOptions } from '../deeds/deedQueries'
import { categoryLabel, STATUS_LABELS } from '../deeds/deedText'
import type { DeedStatus } from '../deeds/deedsApi'
import type { PhotoReportReason } from '../media/mediaApi'
import { useMediaOptions } from '../media/mediaQueries'
import { firstName } from '../report/format'
import { adminApi, adminKeys, type PhotoDecisionRequest, type PhotoReportDetail } from './adminApi'
import { NOTE_MIN, RestoreDialog } from './AdminDialog'
import { decisionError } from './decisionError'
import { formatDateTime, formatLongDate } from './adminFormat'
import { AdminMain, AdminTitle } from './adminUi'
import { useReasonLabel, useReasonPriority } from './reasonLabels'
import { RemovePhotoDialog } from './RemovePhotoDialog'
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

const QUEUE = '/admin/photo-reports'

/**
 * `/admin/photo-reports/:caseId` (photos canvas §3, D-AdminReview / D-AdminDone; M3-DESIGN §6.3):
 * the photo with its status, the deed, the upload record, every report, and Decide (a required
 * note, Keep or Remove). After a decision: the done banner, with Restore while the removed photo
 * is restorable (Q32), or the date it was deleted for good.
 */
export function PhotoReportReviewPage() {
  const { caseId = '' } = useParams()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: adminKeys.photoReport(caseId),
    queryFn: () => adminApi.photoReport(caseId),
    retry: false,
  })

  const onSaved = (detail: PhotoReportDetail) => {
    queryClient.setQueryData(adminKeys.photoReport(caseId), detail)
    void queryClient.invalidateQueries({ queryKey: ['admin', 'photo-reports'] })
  }

  if (query.isPending) return <Loading label="Loading this report…" />
  if (query.isError) {
    return (
      <AdminMain>
        <ReviewLoadError
          error={query.error}
          backTo={QUEUE}
          backLabel="Photo reports"
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
  detail: PhotoReportDetail
  onSaved: (detail: PhotoReportDetail) => void
  onConflict: () => void
}) {
  const { case: kase, photo, deed, reports, decisions } = detail
  const reasonLabel = useReasonLabel()
  const reasonPriority = useReasonPriority()
  const categories = useDeedOptions().data?.categories
  const noteMax = useMediaOptions().data?.decision_note_max ?? DEFAULT_NOTE_MAX
  const [note, setNote] = useState('')
  const [noteTooShort, setNoteTooShort] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [openedAt] = useState(() => Date.now())
  const uploaderFirst = firstName(photo.uploader.display_name)

  const decide = useMutation({
    mutationFn: (data: PhotoDecisionRequest) => adminApi.decidePhotoReport(kase.case_id, data),
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
    mutationFn: (restoreNote: string) => adminApi.restorePhoto(photo.id, { note: restoreNote }),
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
  const restored = kase.decision === 'removed' && photo.state === 'visible'
  const status =
    photo.state === 'removed'
      ? { label: 'Removed', className: 'bg-red-700 text-white' }
      : open
        ? { label: 'Visible · report open', className: 'bg-slate-900/80 text-white' }
        : kase.status === 'closed'
          ? { label: 'Deleted by its owner', className: 'bg-slate-700 text-white' }
          : restored
            ? { label: 'Restored', className: 'bg-emerald-700 text-white' }
            : { label: 'Kept after review', className: 'bg-emerald-700 text-white' }
  const blurred =
    photo.blurred_count === 0
      ? 'No faces were blurred on this photo.'
      : photo.blurred_count === 1
        ? '1 face was blurred on this photo.'
        : `${photo.blurred_count} faces were blurred on this photo.`
  const canRestore =
    photo.state === 'removed' &&
    photo.restorable_until !== null &&
    photo.purged_at === null &&
    Date.parse(photo.restorable_until) > openedAt

  let doneTitle: string
  let doneBody: string
  if (kase.status === 'closed') {
    doneTitle = 'Report closed.'
    doneBody =
      'The photo was deleted by the person who posted it or by the doer, so there’s nothing to decide.'
  } else if (restored) {
    doneTitle = 'Photo restored.'
    doneBody = `${uploaderFirst} now sees that the photo stays. Your note is in the audit log.`
  } else {
    doneTitle = kase.decision === 'kept' ? 'Photo kept.' : 'Photo removed.'
    doneBody = `${uploaderFirst} has been told the outcome. Your note and decision are in the audit log.`
  }

  return (
    <AdminMain>
      <BackToQueue to={QUEUE} label="Photo reports" />
      {!open && (
        <DoneBanner
          title={doneTitle}
          body={doneBody}
          action={
            canRestore && (
              <RestoreButton
                label="Restore photo"
                onClick={() => {
                  restore.reset()
                  setRestoring(true)
                }}
              />
            )
          }
          footnote={
            photo.purged_at
              ? `Deleted for good on ${formatLongDate(photo.purged_at)}.`
              : canRestore && photo.restorable_until
                ? `Can be restored until ${formatLongDate(photo.restorable_until)}.`
                : null
          }
        />
      )}
      <AdminTitle>Review photo report</AdminTitle>
      <div className="flex flex-wrap items-start gap-6">
        <div className="flex min-w-0 flex-[1_1_420px] flex-col gap-2.5">
          <div className="relative flex h-[380px] items-center justify-center overflow-hidden rounded-[14px] bg-slate-200">
            <img
              src={photo.display_url}
              alt={`Reported photo: photo ${photo.position} of ${photo.photo_count} on ${deed.doer.display_name}’s deed`}
              className="size-full object-contain"
            />
            <span
              className={`absolute top-3 left-3 rounded-full px-2.5 py-0.5 text-[13px] font-bold ${status.className}`}
            >
              {status.label}
            </span>
          </div>
          <p className="text-sm text-slate-600">
            Photo {photo.position} of {photo.photo_count} · Uploaded{' '}
            {formatDateTime(photo.uploaded_at)}
          </p>
        </div>
        <div className="flex min-w-0 flex-[1_1_380px] flex-col gap-3.5">
          <ReviewCard title="Deed">
            <p>
              <strong>{deed.doer.display_name}</strong>, recognised by{' '}
              <strong>{deed.reporter.display_name}</strong>
              {photo.uploader.handle === deed.reporter.handle && ' (uploaded the photos)'}
            </p>
            <p className="text-sm text-slate-600">
              {categoryLabel(categories, deed.category)} · {deed.area_name} ·{' '}
              {STATUS_LABELS[deed.status as DeedStatus] ?? deed.status} ·{' '}
              {deed.listed ? 'Shown in feed' : 'Hidden from feed'}
            </p>
          </ReviewCard>
          <ReviewCard title="Upload record">
            <p className="text-[15px]">
              Uploaded by {photo.uploader.display_name} on {formatDateTime(photo.uploaded_at)}.
            </p>
            <p className="text-sm text-slate-600">{blurred}</p>
          </ReviewCard>
          <ReportsCard
            kind="photo"
            reports={reports.map((report) => ({
              priority: reasonPriority(report.reason),
              reason: reasonLabel(report.reason),
              details: report.details,
              who:
                report.reporter.kind === 'member'
                  ? `Member @${report.reporter.handle}`
                  : 'Visitor on a shared link',
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
              help={`${uploaderFirst} is told the outcome. The deed, its verification and points never change.`}
              keepLabel="Keep photo"
              removeLabel="Remove photo"
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
        <RemovePhotoDialog
          uploaderFirstName={uploaderFirst}
          defaultReason={kase.top_reason}
          reasonLabel={reasonLabel}
          busy={decide.isPending}
          error={decide.isError ? decisionError(decide.error) : null}
          onCancel={() => setConfirmRemove(false)}
          onConfirm={(reason: PhotoReportReason) =>
            decide.mutate({ action: 'remove', note: note.trim(), removal_reason: reason })
          }
        />
      )}
      {restoring && (
        <RestoreDialog
          title="Restore this photo?"
          body={`It comes back on the feed, the deed page and shared links. ${uploaderFirst} is told the photo stays.`}
          confirmLabel="Restore photo"
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
