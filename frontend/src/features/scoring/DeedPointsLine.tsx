import { formatWithdrawUntil } from '../deeds/withdrawWindow'
import { useDeedPoints } from './scoringQueries'
import { signedPoints } from './scoringText'

/**
 * The caller's own points line on a deed they did or reported (M4-DESIGN §6.3, §3.3 #5):
 * "+10 points · provisional until 3 Dec, 14:05", or for a capped deed "0 points · Daily limit
 * reached: still counts on your profile". The reason text is the server's `reason_label`. Hidden while loading, on a 404 (not theirs, or
 * pending) and on any error, so the deed page never waits for scoring.
 */
export function DeedPointsLine({ deedId, enabled = true }: { deedId: string; enabled?: boolean }) {
  const points = useDeedPoints(deedId, enabled)
  if (!enabled || !points.data) return null
  const { points: n, reason_label: reasonLabel, capped, provisional_until } = points.data
  const parts = [`${signedPoints(n)} ${Math.abs(n) === 1 ? 'point' : 'points'}`]
  if ((capped || n <= 0) && reasonLabel) parts.push(reasonLabel)
  if (provisional_until) parts.push(`provisional until ${formatWithdrawUntil(provisional_until)}`)
  return (
    <section
      aria-label="Your points for this deed"
      className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3 text-[15px] text-emerald-900"
    >
      <p className="font-semibold">{parts.join(' · ')}</p>
      <p className="text-sm text-emerald-800">Only you can see your points.</p>
    </section>
  )
}
