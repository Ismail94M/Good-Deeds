import { useId, useState } from 'react'
import type { PhotoReportReason } from '../media/mediaApi'
import { REASON_LABELS } from '../media/mediaText'
import { AdminDialog, DialogActions, fieldClass } from './AdminDialog'

const REASONS = Object.keys(REASON_LABELS) as PhotoReportReason[]

/**
 * "Remove this photo?" (photos canvas, D-AdminRemoveConfirm). Also picks the reason the uploader
 * is shown, defaulting to the top report's reason (_interim_, not on the canvas).
 */
export function RemovePhotoDialog({
  uploaderFirstName,
  defaultReason,
  reasonLabel,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  uploaderFirstName: string
  defaultReason: PhotoReportReason
  reasonLabel: (reason: PhotoReportReason) => string
  busy: boolean
  error: string | null
  onConfirm: (reason: PhotoReportReason) => void
  onCancel: () => void
}) {
  const selectId = useId()
  const [reason, setReason] = useState<PhotoReportReason>(defaultReason)
  return (
    <AdminDialog title="Remove this photo?" onDismiss={onCancel} busy={busy}>
      {({ bodyId }) => (
        <>
          <p id={bodyId} className="text-slate-700">
            It disappears from the feed, the deed page and shared links. {uploaderFirstName} is told
            it was removed after review. The deed, its verification and points don’t change. You can
            restore it later.
          </p>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={selectId} className="text-[15px] font-semibold">
              Reason shown to {uploaderFirstName}
            </label>
            <select
              id={selectId}
              value={reason}
              onChange={(event) => setReason(event.target.value as PhotoReportReason)}
              className={`${fieldClass} min-h-12`}
            >
              {REASONS.map((id) => (
                <option key={id} value={id}>
                  {reasonLabel(id)}
                </option>
              ))}
            </select>
          </div>
          {error && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {error}
            </p>
          )}
          <DialogActions
            confirm={busy ? 'Removing…' : 'Remove photo'}
            danger
            busy={busy}
            onConfirm={() => onConfirm(reason)}
            onCancel={onCancel}
          />
        </>
      )}
    </AdminDialog>
  )
}
