/** Copy shared by the My deeds screens (M2-DESIGN §5.3 F6). */

import type { Category, DeedOut, DeedStatus } from './deedsApi'

/** The category's label from `/deeds/options`, or a readable form of the slug. */
export function categoryLabel(categories: Category[] | undefined, slug: string): string {
  const found = categories?.find((c) => c.slug === slug)
  if (found) return found.label
  const words = slug.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export const STATUS_LABELS: Record<DeedStatus, string> = {
  verified: 'Verified',
  withdrawn: 'Withdrawn',
  pending: 'Pending',
  unverified: 'Unverified',
  rejected: 'Rejected',
  removed: 'Removed',
}

/**
 * The doer's name as this screen may show it, or null. A pending deed never names its doer
 * (PRD §6.5), even if a doer were sent: every screen goes through here.
 */
export function visibleDoerName(deed: DeedOut): string | null {
  if (deed.status === 'pending') return null
  return deed.doer?.display_name ?? null
}

/**
 * The who line: "{reporter} recognised you" for the doer (D24), "You recognised {doer}" for the
 * reporter. A pending deed never names its doer (PRD §6.5).
 */
export function whoLine(deed: DeedOut, viewer: 'doer' | 'reporter'): string {
  if (viewer === 'doer') return `${deed.reporter.display_name} recognised you`
  const doer = visibleDoerName(deed)
  return doer ? `You recognised ${doer}` : 'You recorded a deed'
}

/**
 * Q20 (PO 2026-11-17), only on reports about you that don't count: the act already has its
 * `cap` counted reports.
 */
export function extraWitnessNote(cap: number): string {
  return `Extra witness: ${cap} people already reported this act, so this report doesn't add to your count.`
}

/** True for a verified report about the viewer that is beyond the same-act cap. */
export function isExtraWitness(deed: DeedOut): boolean {
  return deed.same_act !== null && !deed.same_act.counts_for_points
}

/** How the deed was checked, for the detail and edit screens. */
export function checkedBy(deed: DeedOut): string {
  return deed.verification_method === 'qr_scan' ? 'QR code and location' : 'Invite link'
}

/** Which side of the deed the signed-in user is on. */
export function viewerRole(deed: DeedOut, handle: string | undefined): 'doer' | 'reporter' {
  return handle && deed.reporter.handle.toLowerCase() === handle.toLowerCase() ? 'reporter' : 'doer'
}
