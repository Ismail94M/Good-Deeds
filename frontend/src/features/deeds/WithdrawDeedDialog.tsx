import { ConfirmDialog } from '../../components/ConfirmDialog'
import { errorMessage } from '../../lib/api'
import { useWithdrawDeed } from './deedQueries'
import type { DeedOut } from './deedsApi'
import { visibleDoerName } from './deedText'

/**
 * "Withdraw this report?" (F6 canvas). Withdrawing is final; the deed stops counting for the
 * doer. "Keep it" is focused first, so Enter doesn't withdraw by accident.
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
  const doer = visibleDoerName(deed) ?? 'them'
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
