/**
 * Shared by the M5 scoring admin screens (deed scoring, override requests, user scoring): the
 * fraud options query (reason codes, note lengths, max points), error copy per API code, and
 * small text helpers (components are in `scoringAdminParts.tsx`). Copy is _interim_ (docs/M5-DESIGN.md §6.2), for PO review.
 */

import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../lib/api'
import { retryUnlessClientError } from '../feed/feedQueries'
import { decisionError } from './decisionError'
import { scoringAdminApi, scoringAdminKeys, type AdminUserRef } from './scoringAdminApi'

export function useScoringFraudOptions() {
  return useQuery({
    queryKey: scoringAdminKeys.fraudOptions,
    queryFn: scoringAdminApi.fraudOptions,
    staleTime: 5 * 60_000,
    retry: retryUnlessClientError,
  })
}

export const NOTHING_TO_OVERRIDE =
  'This report earns no points to change: it is capped, not counted or no longer verified.'

/**
 * 202 from #13: the change waits for a second admin (`n` = `override_second_admin.max_cut_points`;
 * measured from the rule points, up or down, Q79/Q80).
 */
export function secondAdminSent(n: number): string {
  return `Sent for a second admin’s approval: changes of more than ${n} points from the rule points, or to 0, need one.`
}

export const FINAL_MONTH =
  'This month is final: the change shows in the person’s points but not on the final leaderboard.'

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

function codeOf(error: unknown): string | null {
  return error instanceof ApiError ? error.code : null
}

/** Copy for #13 errors (override form). */
export function overrideError(error: unknown, limits: { min: number; max: number }): string {
  switch (codeOf(error)) {
    case 'nothing_to_override':
      return NOTHING_TO_OVERRIDE
    case 'request_pending':
      return 'A change to these points is already waiting for a second admin. It can be decided under Override requests.'
    case 'override_unchanged':
      return 'That wouldn’t change anything. Choose different points, or keep things as they are.'
    case 'points_out_of_range':
      return `Points must be between 0 and ${limits.max}.`
    case 'note_required':
      return `Add a note of at least ${limits.min} characters.`
    case 'invalid_reason_code':
      return 'Choose one of the listed reasons.'
    case 'unknown_case':
      return 'We couldn’t find a case with that ID. Check it, or leave the field empty.'
  }
  if (isNotFound(error)) return 'You can’t change the points of this deed.'
  return decisionError(error)
}

/** Copy for #18 errors (approve / decline a request). */
export function requestDecisionError(error: unknown): string {
  switch (codeOf(error)) {
    case 'request_decided':
      return 'Another admin already decided this request. The list has been reloaded.'
    case 'request_expired':
      return 'This request has expired. If the change is still needed, make it again from the deed’s page.'
    case 'nothing_to_override':
      return `${NOTHING_TO_OVERRIDE} The request can’t be approved.`
  }
  if (isNotFound(error)) {
    return 'This request isn’t available to you any more. The list has been reloaded.'
  }
  return decisionError(error)
}

export const NEEDS_CONFIRMED_CASE = 'Revoking needs a confirmed fraud case about this person.'

/** Copy for #15/#16 errors (revoke). */
export function revokeError(error: unknown): string {
  switch (codeOf(error)) {
    case 'decision_not_final':
      return 'This decision can still be appealed. Revoking is possible once it is final.'
    case 'case_not_confirmed':
      return NEEDS_CONFIRMED_CASE
    case 'already_revoked':
      return 'This has already been revoked. The page has been reloaded.'
  }
  if (isNotFound(error)) return 'We couldn’t find that any more. The page has been reloaded.'
  return decisionError(error)
}

export function personName(user: AdminUserRef | null | undefined): string {
  return user?.display_name ?? 'Unavailable account'
}

export function roleLabel(role: 'doer' | 'reporter'): string {
  return role === 'doer' ? 'Doer' : 'Reporter'
}
