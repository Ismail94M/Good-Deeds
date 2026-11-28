import { useQueryClient } from '@tanstack/react-query'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react'
import { Link } from 'react-router'
import { feedApi, feedKeys, type StoryOut, type StoryRing } from './feedApi'
import { useStoryPerson } from './feedQueries'
import { timeAgo } from './feedText'
import { Avatar, ChevronIcon } from './feedUi'
import { firstUnseen, ROLE_LABEL, ROLE_TONE, storyLine } from './storyText'

const TICK_MS = 100
const HOLD_MS = 250
const SWIPE_PX = 60

type Start = 'unseen' | 'last'

/**
 * Full-screen story viewer (D73). One person at a time: their stories load when they are reached
 * (photos are signed URLs from the API), and only the next photo is fetched ahead. Tap the right
 * or left side for the next or previous story, hold to pause, swipe sideways (or use the arrow
 * keys) for the next or previous person, Space pauses, Escape closes. A story is marked watched
 * when it is shown; the bar refreshes on close so watched rings turn grey.
 */
export function StoryViewer({
  rings,
  startIndex,
  secondsPerStory,
  onClose,
}: {
  rings: StoryRing[]
  startIndex: number
  secondsPerStory: number
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [person, setPerson] = useState(startIndex)
  const [start, setStart] = useState<Start>('unseen')
  // The story picked by tapping; null = this person's starting story.
  const [chosen, setChosen] = useState<number | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [paused, setPaused] = useState(false)
  const [holding, setHolding] = useState(false)
  const [ready, setReady] = useState(false)
  const marked = useRef(new Set<string>())
  const closeRef = useRef<HTMLButtonElement>(null)
  const elapsedRef = useRef(0)

  const ring = rings[person]
  const handle = ring.user.handle ?? ''
  const stories = useStoryPerson(handle)
  const items = stories.data?.items
  // Start at the first unwatched story (or the last, when coming back from the next person).
  const index =
    items === undefined || items.length === 0
      ? null
      : (chosen ?? (start === 'last' ? items.length - 1 : firstUnseen(items)))
  const story = items && index !== null ? items[index] : undefined
  const duration = secondsPerStory * 1000

  const close = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: feedKeys.stories, exact: true })
    onClose()
  }, [onClose, queryClient])

  const goPerson = useCallback(
    (to: number, from: Start) => {
      if (to < 0) return
      if (to >= rings.length) {
        close()
        return
      }
      setPerson(to)
      setStart(from)
      setChosen(null)
    },
    [close, rings.length],
  )

  const next = useCallback(() => {
    if (items && index !== null && index < items.length - 1) setChosen(index + 1)
    else goPerson(person + 1, 'unseen')
  }, [goPerson, index, items, person])

  const prev = useCallback(() => {
    if (index !== null && index > 0) setChosen(index - 1)
    else goPerson(person - 1, 'last')
  }, [goPerson, index, person])

  // A new story: restart its timer; a photo waits until it has loaded.
  const storyKey = story ? `${story.post.deed_id}:${story.role}` : null
  const [shownKey, setShownKey] = useState<string | null>(null)
  if (storyKey !== shownKey) {
    setShownKey(storyKey)
    setElapsed(0)
    setReady(story !== undefined && story.post.photos.length === 0)
  }
  useEffect(() => {
    elapsedRef.current = 0
  }, [storyKey])

  // Mark it watched (once per viewer session).
  useEffect(() => {
    if (!story || storyKey === null || story.seen || marked.current.has(storyKey)) return
    marked.current.add(storyKey)
    feedApi.storySeen(story.post.deed_id, story.role).catch(() => {
      // Not essential: the ring just stays coloured.
    })
  }, [story, storyKey])

  // Fetch the next photo ahead, and the next person's list when this one is on screen.
  useEffect(() => {
    if (!items || index === null) return
    const upcoming = items[index + 1]?.post.photos[0]
    if (upcoming) {
      const img = new Image()
      img.src = upcoming.display_url
    }
    const nextRing = rings[person + 1]
    if (index === items.length - 1 && nextRing?.user.handle) {
      const nextHandle = nextRing.user.handle
      void queryClient.prefetchQuery({
        queryKey: feedKeys.storyPerson(nextHandle),
        queryFn: () => feedApi.storyPerson(nextHandle),
        staleTime: 5 * 60_000,
      })
    }
  }, [index, items, person, queryClient, rings])

  // The timer: moves on by itself when the story's time is up.
  const nextRef = useRef(next)
  useEffect(() => {
    nextRef.current = next
  }, [next])
  const running = story !== undefined && ready && !paused && !holding
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => {
      elapsedRef.current += TICK_MS
      if (elapsedRef.current >= duration) {
        elapsedRef.current = 0
        nextRef.current()
      } else {
        setElapsed(elapsedRef.current)
      }
    }, TICK_MS)
    return () => window.clearInterval(id)
  }, [duration, running])

  // Pause while the tab is hidden; lock page scroll; focus the dialog and restore focus.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) setPaused(true)
    }
    document.addEventListener('visibilitychange', onVisibility)
    const opener = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      document.body.style.overflow = overflow
      opener?.focus()
    }
  }, [])

  // Tap, hold and swipe on the story itself.
  const press = useRef<{ x: number; y: number; timer: number; held: boolean } | null>(null)
  const suppressClick = useRef(false)
  const onPointerDown = (event: PointerEvent) => {
    const timer = window.setTimeout(() => {
      if (press.current) press.current.held = true
      setHolding(true)
    }, HOLD_MS)
    press.current = { x: event.clientX, y: event.clientY, timer, held: false }
  }
  const onPointerUp = (event: PointerEvent) => {
    const p = press.current
    press.current = null
    if (!p) return
    window.clearTimeout(p.timer)
    setHolding(false)
    const dx = event.clientX - p.x
    if (Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(event.clientY - p.y)) {
      suppressClick.current = true
      goPerson(dx < 0 ? person + 1 : person - 1, dx < 0 ? 'unseen' : 'last')
    } else if (p.held) {
      suppressClick.current = true
    }
  }
  const onPointerCancel = () => {
    if (press.current) window.clearTimeout(press.current.timer)
    press.current = null
    setHolding(false)
  }
  const tap = (go: () => void) => () => {
    if (suppressClick.current) {
      suppressClick.current = false
      return
    }
    go()
  }

  // Story keys work wherever focus is (a tapped control can disappear between stories).
  const keys = useRef({ close, next, prev })
  useEffect(() => {
    keys.current = { close, next, prev }
  }, [close, next, prev])
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        keys.current.close()
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        keys.current.next()
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        keys.current.prev()
      } else if (
        event.key === ' ' &&
        // A focused button or link handles Space itself.
        !(event.target instanceof HTMLButtonElement || event.target instanceof HTMLAnchorElement)
      ) {
        event.preventDefault()
        setPaused((p) => !p)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // Keep Tab inside the dialog.
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Tab') return
    const all = event.currentTarget.querySelectorAll<HTMLElement>(
      'a[href], button:not([tabindex="-1"])',
    )
    if (all.length === 0) return
    const first = all[0]
    const last = all[all.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const tone = story ? ROLE_TONE[story.role] : null
  const progress = Math.min(1, elapsed / duration)
  const ownerIsMe = ring.is_me

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${ownerIsMe ? 'Your' : `${ring.user.display_name}’s`} stories`}
      onKeyDown={onKeyDown}
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950 sm:bg-slate-950/90"
    >
      {person > 0 && (
        <button
          type="button"
          onClick={() => goPerson(person - 1, 'last')}
          aria-label="Previous person"
          className="absolute left-6 hidden size-11 items-center justify-center rounded-full bg-white/15 text-white hover:bg-white/25 focus-visible:outline-2 focus-visible:outline-white sm:flex"
        >
          <ChevronIcon dir="left" />
        </button>
      )}
      <div className="relative flex h-svh w-full flex-col overflow-hidden bg-black text-white sm:h-[min(92svh,860px)] sm:max-w-[440px] sm:rounded-2xl">
        {/* Media */}
        <div className="absolute inset-0">
          {story ? (
            story.post.photos.length > 0 ? (
              <img
                key={storyKey ?? ''}
                src={story.post.photos[0].display_url}
                alt={`Photo of the deed: ${story.post.category_label}`}
                onLoad={() => setReady(true)}
                onError={() => setReady(true)}
                className="size-full object-contain"
              />
            ) : (
              <div
                className={`flex size-full items-center justify-center bg-gradient-to-br ${tone?.card ?? ''} p-8`}
              >
                <p className="line-clamp-[10] text-center text-2xl leading-snug font-semibold">
                  {story.post.description}
                </p>
              </div>
            )
          ) : (
            <div className="flex size-full items-center justify-center">
              {stories.isError ? (
                <div className="flex flex-col items-center gap-3 px-8 text-center">
                  <p>We couldn’t load these stories.</p>
                  <button
                    type="button"
                    onClick={() => void stories.refetch()}
                    className="rounded-lg bg-white px-4 py-2 font-semibold text-slate-900"
                  >
                    Try again
                  </button>
                </div>
              ) : items?.length === 0 ? (
                <div className="flex flex-col items-center gap-3 px-8 text-center">
                  <p>These stories have ended.</p>
                  <button
                    type="button"
                    onClick={() => goPerson(person + 1, 'unseen')}
                    className="rounded-lg bg-white px-4 py-2 font-semibold text-slate-900"
                  >
                    Next
                  </button>
                </div>
              ) : (
                <p role="status">Loading stories…</p>
              )}
            </div>
          )}
        </div>

        {/* Tap zones: previous (left third), next (the rest). Hold to pause, swipe for people. */}
        {story && (
          <div
            className="absolute inset-0 flex touch-pan-y select-none"
            onPointerDown={onPointerDown}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerCancel}
            onPointerLeave={onPointerCancel}
          >
            <button
              type="button"
              tabIndex={-1}
              aria-label="Previous story"
              onClick={tap(prev)}
              className="h-full w-1/3 cursor-w-resize"
            />
            <button
              type="button"
              tabIndex={-1}
              aria-label="Next story"
              onClick={tap(next)}
              className="h-full flex-1 cursor-e-resize"
            />
          </div>
        )}

        {/* Top: progress, person, controls */}
        <div className="pointer-events-none relative flex flex-col gap-3 bg-gradient-to-b from-black/70 to-transparent px-3 pt-3 pb-8">
          <div aria-hidden className="flex gap-1">
            {(items ?? []).map((s, i) => (
              <span
                key={`${s.post.deed_id}:${s.role}`}
                data-testid="story-progress"
                className="h-[3px] flex-1 overflow-hidden rounded-full bg-white/35"
              >
                <span
                  className="block h-full bg-white"
                  style={{
                    width: `${index === null || i > index ? 0 : i < index ? 100 : progress * 100}%`,
                  }}
                />
              </span>
            ))}
          </div>
          <div className="flex items-center gap-2.5">
            <Avatar user={ring.user} size="md" />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate font-semibold">
                {ownerIsMe ? 'You' : ring.user.display_name}
              </span>
              {story && (
                <span className="flex items-center gap-2 text-xs text-white/85">
                  <span
                    className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${tone?.chip ?? ''}`}
                  >
                    {ROLE_LABEL[story.role]}
                  </span>
                  <time dateTime={story.post.published_at}>{timeAgo(story.post.published_at)}</time>
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => setPaused((p) => !p)}
              aria-label={paused ? 'Play' : 'Pause'}
              aria-pressed={paused}
              className="pointer-events-auto flex size-10 items-center justify-center rounded-full hover:bg-white/15 focus-visible:outline-2 focus-visible:outline-white"
            >
              {paused ? <PlayIcon /> : <PauseIcon />}
            </button>
            <button
              ref={closeRef}
              type="button"
              onClick={close}
              aria-label="Close stories"
              className="pointer-events-auto flex size-10 items-center justify-center rounded-full hover:bg-white/15 focus-visible:outline-2 focus-visible:outline-white"
            >
              <CloseIcon />
            </button>
          </div>
        </div>

        {/* Bottom: what happened */}
        {story && items && index !== null && (
          <Caption
            story={story}
            ownerIsMe={ownerIsMe}
            position={`Story ${index + 1} of ${items.length}`}
            onOpenDeed={close}
          />
        )}
      </div>
      {person < rings.length - 1 && (
        <button
          type="button"
          onClick={() => goPerson(person + 1, 'unseen')}
          aria-label="Next person"
          className="absolute right-6 hidden size-11 items-center justify-center rounded-full bg-white/15 text-white hover:bg-white/25 focus-visible:outline-2 focus-visible:outline-white sm:flex"
        >
          <ChevronIcon dir="right" />
        </button>
      )}
    </div>
  )
}

function Caption({
  story,
  ownerIsMe,
  position,
  onOpenDeed,
}: {
  story: StoryOut
  ownerIsMe: boolean
  position: string
  onOpenDeed: () => void
}) {
  const { post } = story
  const morePhotos = post.photos.length - 1
  return (
    <div
      data-testid="story-caption"
      className="pointer-events-none relative mt-auto flex flex-col gap-1.5 bg-gradient-to-t from-black/85 via-black/60 to-transparent px-4 pt-12 pb-5"
    >
      <p className="sr-only" aria-live="polite">
        {position}
      </p>
      <p className="text-base leading-snug font-bold">{storyLine(story, ownerIsMe)}</p>
      <p className="text-sm text-white/85">
        {post.category_label} · {post.area_name}
      </p>
      {post.photos.length > 0 && (
        <p className="line-clamp-3 text-sm text-white/95">{post.description}</p>
      )}
      <div className="mt-1 flex items-center gap-3">
        <Link
          to={`/deeds/${encodeURIComponent(post.deed_id)}`}
          onClick={onOpenDeed}
          className="pointer-events-auto inline-flex min-h-10 items-center rounded-full bg-white px-4 text-sm font-semibold text-slate-900 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
        >
          View deed
        </Link>
        {morePhotos > 0 && (
          <span className="text-xs text-white/80">
            +{morePhotos} more {morePhotos === 1 ? 'photo' : 'photos'}
          </span>
        )}
      </div>
    </div>
  )
}

function PauseIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="size-5" fill="currentColor">
      <rect x="6" y="5" width="4" height="14" rx="1" />
      <rect x="14" y="5" width="4" height="14" rx="1" />
    </svg>
  )
}

function PlayIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="size-5" fill="currentColor">
      <path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" />
    </svg>
  )
}

function CloseIcon() {
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
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}
