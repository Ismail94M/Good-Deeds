/** Default avatars (docs/M6-DESIGN.md D120, PO Q88 D): initials on a soft colour, or a silhouette. */

import { initials } from '../../report/format'

const AVATAR_TONES = [
  'bg-brand-100 text-brand-900',
  'bg-emerald-100 text-emerald-800',
  'bg-amber-200 text-amber-900',
  'bg-pink-100 text-pink-900',
  'bg-indigo-100 text-indigo-800',
]

/** A stable colour per handle, so the same person looks the same on every screen. */
export function toneFor(handle: string): string {
  let sum = 0
  for (const ch of handle) sum = (sum + ch.charCodeAt(0)) % 997
  return AVATAR_TONES[sum % AVATAR_TONES.length]
}

export const SILHOUETTE_TONE = 'bg-slate-200 text-slate-500'

/** Neutral person shape for "Unavailable account" and deleted users: no initials of a hidden name. */
export function Silhouette() {
  return (
    <svg
      aria-hidden
      data-testid="avatar-silhouette"
      viewBox="0 0 24 24"
      className="size-[62%]"
      fill="currentColor"
    >
      <circle cx="12" cy="8.5" r="4.2" />
      <path d="M3.8 21c.7-4.4 4-7 8.2-7s7.5 2.6 8.2 7z" />
    </svg>
  )
}

/** The inside of a default avatar: initials, or the silhouette when the person is unavailable. */
export function DefaultAvatarContent({
  name,
  unavailable,
}: {
  name: string
  unavailable: boolean
}) {
  if (unavailable) return <Silhouette />
  return <>{initials(name)}</>
}
