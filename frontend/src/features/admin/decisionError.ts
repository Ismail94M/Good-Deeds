import { ApiError, errorMessage } from '../../lib/api'

/** Copy for decision and restore errors (§6.3; `conflict_of_interest` is _interim_). */
export function decisionError(error: unknown): string {
  const code = error instanceof ApiError ? error.code : null
  if (code === 'conflict_of_interest') {
    return 'Another admin needs to review this one: you’re part of this deed.'
  }
  if (code === 'case_not_open') return 'Someone already decided this report. It has been reloaded.'
  if (code === 'avatar_replaced') {
    return 'The owner has added a newer profile picture, so this one can’t be restored.'
  }
  if (code === 'not_restorable') return 'This can’t be restored any more.'
  if (code === 'rate_limited') return 'You’re deciding very fast. Wait a minute, then try again.'
  if (error instanceof ApiError && error.status === 422) {
    return error.fieldErrors.note ?? error.detail
  }
  return errorMessage(error)
}
