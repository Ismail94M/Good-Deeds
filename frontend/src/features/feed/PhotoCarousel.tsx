import { useRef, type KeyboardEvent } from 'react'
import type { PhotoOut } from '../media/mediaApi'
import { ChevronIcon, focusRing } from './feedUi'

/**
 * Photos of a deed (F7 card): swipe on phones (scroll snap), arrow keys or the side buttons
 * elsewhere, "1 / n" counter and dots. Only the photos it is given exist: a removed photo is
 * simply absent and the counter counts what is left (Q33). Controlled, so the card knows which
 * photo is showing (the one "Report photo" reports).
 */
export function PhotoCarousel({
  photos,
  index,
  onIndexChange,
  onImageError,
  onRemove,
}: {
  photos: PhotoOut[]
  index: number
  onIndexChange: (index: number) => void
  /** A signed URL failed to load (likely expired): the owner refetches (§4.7). */
  onImageError?: () => void
  /** The doer of a verified deed may remove any photo (Q29): shows a ✕ on the current one. */
  onRemove?: (photo: PhotoOut) => void
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const count = photos.length
  if (count === 0) return null
  const current = Math.min(index, count - 1)

  const go = (next: number) => {
    const clamped = Math.max(0, Math.min(count - 1, next))
    onIndexChange(clamped)
    const track = trackRef.current
    if (track && typeof track.scrollTo === 'function') {
      track.scrollTo({ left: clamped * track.clientWidth, behavior: 'smooth' })
    }
  }

  const onScroll = () => {
    const track = trackRef.current
    if (!track || track.clientWidth === 0) return
    const next = Math.round(track.scrollLeft / track.clientWidth)
    if (next !== current && next >= 0 && next < count) onIndexChange(next)
  }

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      go(current + 1)
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      go(current - 1)
    }
  }

  const multi = count > 1
  return (
    <div
      role={multi ? 'region' : undefined}
      aria-roledescription={multi ? 'carousel' : undefined}
      aria-label={multi ? 'Photos' : undefined}
      tabIndex={multi ? 0 : undefined}
      onKeyDown={multi ? onKeyDown : undefined}
      className={`group relative bg-slate-100 ${focusRing}`}
    >
      <div
        ref={trackRef}
        onScroll={multi ? onScroll : undefined}
        className="flex h-65 snap-x snap-mandatory overflow-x-auto overflow-y-hidden [scrollbar-width:none] sm:h-90"
      >
        {photos.map((photo, i) => (
          <img
            key={photo.id}
            src={photo.display_url}
            width={photo.width}
            height={photo.height}
            alt={`Photo ${i + 1} of ${count}`}
            aria-hidden={multi && i !== current ? true : undefined}
            loading={i === 0 ? 'eager' : 'lazy'}
            decoding="async"
            draggable={false}
            onError={onImageError}
            className="h-full w-full shrink-0 snap-center object-cover"
          />
        ))}
      </div>
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove photo ${current + 1} of ${count}`}
          onClick={() => onRemove(photos[current])}
          className={`absolute top-2.5 left-2.5 flex size-11 items-center justify-center rounded-full bg-slate-900/70 text-white hover:bg-slate-900 ${focusRing}`}
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      )}
      {multi && (
        <>
          <span className="absolute top-2.5 right-2.5 rounded-full bg-slate-900/70 px-2.5 py-0.5 text-xs font-semibold text-white">
            {current + 1} / {count}
          </span>
          <span
            aria-hidden
            className="absolute inset-x-0 bottom-2.5 flex justify-center gap-1.5"
            data-testid="carousel-dots"
          >
            {photos.map((photo, i) => (
              <span
                key={photo.id}
                className={`size-1.75 rounded-full ${i === current ? 'bg-white' : 'bg-white/55'}`}
              />
            ))}
          </span>
          {current > 0 && (
            <button
              type="button"
              aria-label="Previous photo"
              tabIndex={-1}
              onClick={() => go(current - 1)}
              className={`absolute top-1/2 left-2 hidden size-10 -translate-y-1/2 items-center justify-center rounded-full bg-white/85 text-slate-800 shadow sm:flex ${focusRing}`}
            >
              <ChevronIcon dir="left" />
            </button>
          )}
          {current < count - 1 && (
            <button
              type="button"
              aria-label="Next photo"
              tabIndex={-1}
              onClick={() => go(current + 1)}
              className={`absolute top-1/2 right-2 hidden size-10 -translate-y-1/2 items-center justify-center rounded-full bg-white/85 text-slate-800 shadow sm:flex ${focusRing}`}
            >
              <ChevronIcon dir="right" />
            </button>
          )}
        </>
      )}
    </div>
  )
}
