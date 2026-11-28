import { useState } from 'react'
import { Link } from 'react-router'
import type { StoryRing } from './feedApi'
import { useStoryRings } from './feedQueries'
import { Avatar, focusRing } from './feedUi'
import { ROLE_TONE, ringBackground, ringKind, ringLabel } from './storyText'
import { StoryViewer } from './StoryViewer'

/**
 * Good Deeds Stories bar (PO 2026-11-23, D73), at the top of the home feed: everyone in my
 * network (people I follow and my followers, and me) with a deed verified in the last 24 hours.
 * Blue ring = they reported a deed they witnessed; green = their own deed was verified; half and
 * half = both; grey = all watched. Only counts load here; a person's photos load when their ring
 * is opened. Hidden when the stories can't be loaded (the feed below still works).
 */
export function StoriesBar() {
  const rings = useStoryRings()
  const [open, setOpen] = useState<number | null>(null)

  if (rings.isError) return null
  if (!rings.data) {
    return (
      <section aria-label="Loading stories" aria-busy="true" className="-mx-1 flex gap-3 px-1 py-1">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="flex w-17 flex-col items-center gap-1.5">
            <span className="size-17 animate-pulse rounded-full bg-slate-200" />
            <span className="h-2.5 w-12 animate-pulse rounded bg-slate-200" />
          </span>
        ))}
      </section>
    )
  }
  const items = rings.data.items
  const hasMine = items.some((r) => r.is_me)
  return (
    <section aria-labelledby="stories-h" className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="stories-h" className="text-sm font-bold text-slate-900">
          Stories
        </h2>
        <p className="flex items-center gap-3 text-xs text-slate-600">
          <Key tone={ROLE_TONE.reported.ring} label="Reported" />
          <Key tone={ROLE_TONE.verified.ring} label="Verified" />
        </p>
      </div>
      {/* scroll-px-4: snap points respect the gutter, or the first ring snaps flush to the
          screen edge on a phone. */}
      <ul className="-mx-4 flex snap-x scroll-px-4 gap-3 overflow-x-auto px-4 pt-0.5 pb-1 [scrollbar-width:none]">
        {!hasMine && (
          <li className="snap-start">
            <Link
              to="/report"
              className={`flex w-17 flex-col items-center gap-1 rounded-xl ${focusRing}`}
            >
              <span className="flex size-17 items-center justify-center rounded-full border-2 border-dashed border-slate-300 bg-white text-slate-500">
                <PlusIcon />
              </span>
              <span className="w-full truncate text-center text-xs text-slate-700">
                Report a deed
              </span>
            </Link>
          </li>
        )}
        {items.map((ring, index) => (
          <li key={ring.user.handle} className="snap-start">
            <RingButton ring={ring} onOpen={() => setOpen(index)} />
          </li>
        ))}
      </ul>
      {items.length === 0 && (
        <p className="text-sm text-slate-600">
          When people you follow, or who follow you, do or witness a good deed, it shows here for{' '}
          {rings.data.window_hours} hours.
        </p>
      )}
      {open !== null && (
        <StoryViewer
          rings={items}
          startIndex={open}
          secondsPerStory={rings.data.seconds_per_story}
          onClose={() => setOpen(null)}
        />
      )}
    </section>
  )
}

function RingButton({ ring, onOpen }: { ring: StoryRing; onOpen: () => void }) {
  const seen = ring.unseen_count === 0
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={ringLabel(ring)}
      data-testid="story-ring"
      data-seen={seen}
      className={`flex w-17 flex-col items-center gap-1 rounded-xl ${focusRing}`}
    >
      <span
        aria-hidden
        data-ring={ringKind(ring)}
        className="rounded-full p-[3px]"
        style={{ background: ringBackground(ring) }}
      >
        <span className="block rounded-full bg-white p-[2px]">
          <Avatar user={ring.user} size="story" />
        </span>
      </span>
      <span
        className={`w-full truncate text-center text-xs ${seen ? 'text-slate-500' : 'font-semibold text-slate-800'}`}
      >
        {ring.is_me ? 'Your story' : ring.user.display_name}
      </span>
    </button>
  )
}

function Key({ tone, label }: { tone: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span aria-hidden className="size-2.5 rounded-full" style={{ background: tone }} />
      {label}
    </span>
  )
}

function PlusIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-6"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}
