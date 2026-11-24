import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, errorMessage } from '../../lib/api'
import { useWithdrawDeed } from './deedQueries'
import { deedsKeys } from './deedsApi'
import type { DeedOut } from './deedsApi'
import { visibleDoerName } from './deedText'

/**
 * "Withdraw this report?" (F6 canvas). Withdrawing is final; the deed stops counting for the
 * doer. "Keep it" is focused first, so Enter doesn't withdraw by accident. Past the 24 h window
 * (M4 D64) the server answers 409 `withdraw_window_closed`: the dialog shows its message ("You
 * can no longer withdraw this deed. It was verified more than 24 hours ago.") with one OK
 * button, and the deed is reloaded so the Withdraw button goes.
 */
export function WithdrawDeedDialog({
  deed,
  onClose,
  onWithdrawn,
}: {
  deed: DeedOut
  onClose: () => void
  onWithdrawn?: (deed: DeedOut) => void
}) {
  const withdraw = useWithdrawDeed((updated) => {
    onWithdrawn?.(updated)
    onClose()
  })
  const queryClient = useQueryClient()
  const doer = visibleDoerName(deed) ?? 'them'
  const windowClosed =
    withdraw.error instanceof ApiError && withdraw.error.code === 'withdraw_window_closed'
  const close = () => {
    if (windowClosed) {
      void queryClient.invalidateQueries({ queryKey: deedsKeys.deed(deed.id) })
      void queryClient.invalidateQueries({ queryKey: ['deeds', 'mine'] })
    }
    onClose()
  }
  if (windowClosed) {
    return (
      <ConfirmDialog
        title="You can’t withdraw this report"
        body={errorMessage(withdraw.error)}
        onDismiss={close}
        actions={[{ label: 'OK', onClick: close, variant: 'secondary', autoFocus: true }]}
      />
    )
  }
  return (
    <ConfirmDialog
      title="Withdraw this report?"
      body={`This can’t be undone. The deed stops counting for ${doer}.`}
      onDismiss={onClose}
      busy={withdraw.isPending}
      error={withdraw.error ? errorMessage(withdraw.error) : null}
      actions={[
        {
          label: withdraw.isPending ? 'Withdrawing…' : 'Withdraw report',
          onClick: () => withdraw.mutate(deed.id),
          variant: 'danger',
        },
        { label: 'Keep it', onClick: onClose, variant: 'secondary', autoFocus: true },
      ]}
    />
  )
}
