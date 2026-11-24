import { ConfirmDialog } from '../../components/ConfirmDialog'

/**
 * "Cancel this report?" (PO decision Q19 = A, 2026-11-17). Cancelling leaves the scan unused,
 * but the pair's day is already taken, so the deed form asks first. Keep writing is the
 * default (focused) choice; Esc keeps writing. Focus goes back to the opener on close.
 */
export function CancelReportDialog({
  doerFirstName,
  onKeep,
  onCancelReport,
}: {
  doerFirstName: string
  onKeep: () => void
  onCancelReport: () => void
}) {
  return (
    <ConfirmDialog
      title="Cancel this report?"
      body={`You can’t record ${doerFirstName} again until tomorrow.`}
      onDismiss={onKeep}
      actions={[
        { label: 'Keep writing', onClick: onKeep, variant: 'primary', autoFocus: true },
        { label: 'Cancel report', onClick: onCancelReport, variant: 'secondary' },
      ]}
    />
  )
}
