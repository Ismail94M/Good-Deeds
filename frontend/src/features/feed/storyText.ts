/** Copy and colours for Good Deeds Stories (D73). */

import type { StoryOut, StoryRing, StoryRole, UserRef } from './feedApi'

export const ROLE_LABEL: Record<StoryRole, string> = {
  reported: 'Reported',
  verified: 'Verified',
}

/** Ring and chip colours: blue for reported (witnessed), green for verified (own deed). */
export const ROLE_TONE: Record<StoryRole, { ring: string; chip: string; card: string }> = {
  reported: {
    ring: '#3b82f6',
    chip: 'bg-blue-500 text-white',
    card: 'from-blue-600 to-blue-900',
  },
  verified: {
    ring: '#10b981',
    chip: 'bg-emerald-500 text-white',
    card: 'from-emerald-600 to-emerald-900',
  },
}

export type RingKind = 'reported' | 'verified' | 'both' | 'seen'

export function ringKind(ring: StoryRing): RingKind {
  if (ring.unseen_count === 0) return 'seen'
  if (ring.reported_count > 0 && ring.verified_count > 0) return 'both'
  return ring.verified_count > 0 ? 'verified' : 'reported'
}

/** The ring's background: blue, green, half and half when the person has both, grey once seen. */
export function ringBackground(ring: StoryRing): string {
  const blue = ROLE_TONE.reported.ring
  const green = ROLE_TONE.verified.ring
  return {
    seen: '#cbd5e1',
    reported: blue,
    verified: green,
    both: `conic-gradient(${blue} 0 180deg, ${green} 180deg 360deg)`,
  }[ringKind(ring)]
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Screen-reader label for a ring button. */
export function ringLabel(ring: StoryRing): string {
  const who = ring.is_me ? 'Your stories' : `${ring.user.display_name}’s stories`
  const parts = [
    ring.verified_count > 0 ? `${ring.verified_count} verified` : null,
    ring.reported_count > 0 ? `${ring.reported_count} reported` : null,
  ].filter(Boolean)
  const fresh = ring.unseen_count > 0 ? plural(ring.unseen_count, 'new', 'new') : 'all seen'
  return `${who}: ${parts.join(', ')}, ${fresh}`
}

function name(user: UserRef, you: boolean): string {
  return you ? 'You' : user.display_name
}

/**
 * "Shiva reported a kindness act by Rahul" (blue) or "Rahul’s kindness act was verified by
 * Shiva" (green), from the ring owner's side.
 */
export function storyLine(story: StoryOut, ownerIsMe: boolean): string {
  const { doer, reporter, my_role } = story.post
  if (story.role === 'reported') {
    const other = my_role === 'doer' ? 'you' : doer.display_name
    return `${name(reporter, ownerIsMe)} reported a kindness act by ${other}`
  }
  const by = my_role === 'reporter' ? 'you' : reporter.display_name
  const owner = ownerIsMe ? 'Your' : `${doer.display_name}’s`
  return `${owner} kindness act was verified by ${by}`
}

/** Where a person's stories start: the first one I haven't watched, else the first. */
export function firstUnseen(items: StoryOut[]): number {
  const index = items.findIndex((s) => !s.seen)
  return index === -1 ? 0 : index
}
