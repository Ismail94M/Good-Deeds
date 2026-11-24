import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { Badge } from './scoringApi'
import { badgeInfo, FAMILY, LEVEL, ladderText, type BadgeFamily, type BadgeInfo } from './badges'

type Opened = { slug: string; by: 'hover' | 'click' } | null

/**
 * Achievement medals (PO 2026-11-24). Each badge is a round medal: an icon on its ladder's
 * colour (Kindness rose, Witness indigo), a rim that grows from starter to bronze, silver and
 * gold, and its name underneath. Hover, focus or tap shows its name, how it was earned and its
 * level. `fresh` badges play a short unlock animation (never with reduced motion); `locked`
 * medals are the owner's next badges, shown greyed with a lock.
 */
export function BadgeShelf({
  badges,
  descriptions,
  locked = [],
  fresh,
  emptyText = 'No badges yet.',
}: {
  badges: Badge[]
  descriptions?: Record<string, string>
  locked?: BadgeInfo[]
  fresh?: ReadonlySet<string>
  emptyText?: string
}) {
  const [opened, setOpened] = useState<Opened>(null)
  const shelf = useRef<HTMLUListElement>(null)
  const earned = badges.map((b) => badgeInfo(b, descriptions?.[b.slug]))

  useEffect(() => {
    if (!opened) return
    const onDown = (event: PointerEvent) => {
      if (!shelf.current?.contains(event.target as Node)) setOpened(null)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpened(null)
    }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [opened])

  if (earned.length === 0 && locked.length === 0) {
    return <p className="text-sm text-slate-600">{emptyText}</p>
  }

  const freshOrder = earned.filter((b) => fresh?.has(b.slug)).map((b) => b.slug)
  return (
    <ul ref={shelf} className="grid grid-cols-4 gap-x-2 gap-y-4 sm:grid-cols-6">
      {earned.map((info) => {
        const isFresh = fresh?.has(info.slug) ?? false
        return (
          <Medal
            key={info.slug}
            info={info}
            fresh={isFresh}
            delay={isFresh ? freshOrder.indexOf(info.slug) * 140 : 0}
            opened={opened}
            setOpened={setOpened}
          />
        )
      })}
      {locked.map((info) => (
        <Medal key={`next-${info.slug}`} info={info} locked opened={opened} setOpened={setOpened} />
      ))}
    </ul>
  )
}

function Medal({
  info,
  locked = false,
  fresh = false,
  delay = 0,
  opened,
  setOpened,
}: {
  info: BadgeInfo
  locked?: boolean
  fresh?: boolean
  delay?: number
  opened: Opened
  setOpened: (next: Opened | ((current: Opened) => Opened)) => void
}) {
  const id = useId()
  const key = locked ? `next-${info.slug}` : info.slug
  const open = opened?.slug === key
  const family = FAMILY[info.family]
  const detail = locked
    ? `Locked. Next on your ${family.name} path: ${info.description}.`
    : `${info.description}. ${ladderText(info)}.`

  return (
    <li className="relative flex justify-center">
      <button
        type="button"
        aria-describedby={`${id}-d`}
        aria-expanded={open}
        data-testid="badge-medal"
        data-level={LEVEL[info.level].name.toLowerCase()}
        data-family={info.family}
        data-locked={locked || undefined}
        data-fresh={fresh || undefined}
        onPointerEnter={(e) => {
          if (e.pointerType === 'mouse')
            setOpened((cur) => (cur?.slug === key ? cur : { slug: key, by: 'hover' }))
        }}
        onPointerLeave={(e) => {
          if (e.pointerType === 'mouse')
            setOpened((cur) => (cur?.slug === key && cur.by === 'hover' ? null : cur))
        }}
        onBlur={() => setOpened((cur) => (cur?.slug === key ? null : cur))}
        onClick={() =>
          setOpened((cur) =>
            cur?.slug === key && cur.by === 'click' ? null : { slug: key, by: 'click' },
          )
        }
        className="group flex w-full max-w-20 flex-col items-center gap-1.5 rounded-xl px-0.5 py-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700"
      >
        <MedalFace info={info} locked={locked} fresh={fresh} delay={delay} />
        <span
          className={`line-clamp-2 text-center text-[11.5px] leading-tight ${
            locked ? 'text-slate-500' : 'font-semibold text-slate-800'
          }`}
        >
          {locked && (
            <span className="block text-[10px] font-semibold tracking-wide text-slate-400 uppercase">
              Next
            </span>
          )}
          {info.label}
        </span>
        {fresh && <span className="sr-only"> (new)</span>}
        <span id={`${id}-d`} className="sr-only">
          {detail}
        </span>
      </button>
      {open && <Details info={info} locked={locked} />}
    </li>
  )
}

function MedalFace({
  info,
  locked,
  fresh,
  delay,
}: {
  info: BadgeInfo
  locked: boolean
  fresh: boolean
  delay: number
}) {
  const family = FAMILY[info.family]
  const level = LEVEL[info.level]
  const shine = !locked && info.level >= 2
  return (
    <span
      aria-hidden
      className={`relative block size-14 transition-transform duration-200 group-hover:-translate-y-0.5 ${
        fresh ? 'badge-unlock' : ''
      }`}
      style={fresh ? { animationDelay: `${delay}ms` } : undefined}
    >
      {fresh && (
        <span
          className="badge-glow absolute inset-0 rounded-full"
          style={{ animationDelay: `${delay + 250}ms` }}
        />
      )}
      <span
        className={`block size-full rounded-full ${info.level === 0 || locked ? 'p-[2px]' : 'p-[3px]'}`}
        style={{
          background: locked
            ? 'repeating-linear-gradient(45deg,#e2e8f0 0 4px,#f1f5f9 4px 8px)'
            : level.rim,
          boxShadow: locked
            ? 'none'
            : `0 4px 10px -2px ${family.glow}, 0 1px 2px rgba(15,23,42,0.18)`,
        }}
      >
        <span
          className="relative flex size-full items-center justify-center overflow-hidden rounded-full"
          style={{
            background: locked ? '#f1f5f9' : family.face,
            boxShadow: locked
              ? 'inset 0 0 0 1px #e2e8f0'
              : 'inset 0 2px 3px rgba(255,255,255,0.45), inset 0 -3px 5px rgba(15,23,42,0.18)',
          }}
        >
          {/* Top highlight: a soft glassy curve on every earned medal. */}
          {!locked && (
            <span className="absolute inset-x-1.5 top-0.5 h-1/2 rounded-full bg-gradient-to-b from-white/45 to-white/0" />
          )}
          <FamilyIcon family={info.family} locked={locked} />
          {shine && (
            <span
              className={`badge-shine absolute inset-y-0 -left-1/2 w-1/3 bg-white/45 ${info.level === 3 ? 'badge-shine-loop' : ''}`}
            />
          )}
        </span>
      </span>
      {locked ? (
        <span className="absolute -right-0.5 -bottom-0.5 flex size-5 items-center justify-center rounded-full bg-white text-slate-500 shadow ring-1 ring-slate-200">
          <LockIcon />
        </span>
      ) : (
        info.mark && (
          <span
            className="absolute -bottom-1.5 left-1/2 min-w-6 -translate-x-1/2 rounded-full bg-white px-1.5 text-center text-[10px] leading-4 font-extrabold shadow-sm"
            style={{
              color: family.ink,
              boxShadow: `0 0 0 1.5px ${info.level === 3 ? '#f59e0b' : info.level === 2 ? '#94a3b8' : '#c08250'}, 0 1px 2px rgba(15,23,42,0.2)`,
            }}
          >
            {info.mark}
          </span>
        )
      )}
      {!locked && info.level === 3 && (
        <span className="absolute -top-1 -right-1 text-amber-400 drop-shadow">
          <StarIcon className="size-4" />
        </span>
      )}
      {fresh &&
        [0, 1, 2].map((i) => (
          <span
            key={i}
            className={`badge-sparkle absolute text-amber-400 badge-sparkle-${i}`}
            style={{ animationDelay: `${delay + 350 + i * 90}ms` }}
          >
            <StarIcon className="size-2.5" />
          </span>
        ))}
    </span>
  )
}

/** The details card above a medal; kept inside the screen on narrow phones. */
function Details({ info, locked }: { info: BadgeInfo; locked: boolean }) {
  const card = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const el = card.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const margin = 8
    let dx = 0
    if (rect.left < margin) dx = margin - rect.left
    else if (rect.right > window.innerWidth - margin) dx = window.innerWidth - margin - rect.right
    el.style.marginLeft = `${dx}px`
  }, [])
  const family = FAMILY[info.family]
  return (
    <div
      ref={card}
      aria-hidden
      data-testid="badge-details"
      className="badge-pop pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 w-52 -translate-x-1/2 rounded-xl bg-slate-900 px-3 py-2.5 text-left text-white shadow-xl"
    >
      <p className="text-sm font-semibold">{info.label}</p>
      <p className="mt-0.5 text-xs text-slate-200">{info.description}</p>
      <p
        className="mt-1.5 text-[11px] font-medium"
        style={{ color: locked ? '#cbd5e1' : '#fcd34d' }}
      >
        {locked ? `Locked · next on your ${family.name} path` : ladderText(info)}
      </p>
    </div>
  )
}

function FamilyIcon({ family, locked }: { family: BadgeFamily; locked: boolean }) {
  const cls = `relative size-7 ${locked ? 'text-slate-400' : 'text-white drop-shadow-[0_1px_1px_rgba(15,23,42,0.35)]'}`
  if (family === 'recognised') {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="currentColor">
        <path d="M12 20.3s-7.6-4.5-7.6-10.1A4.3 4.3 0 0 1 12 7.5a4.3 4.3 0 0 1 7.6 2.7c0 5.6-7.6 10.1-7.6 10.1z" />
      </svg>
    )
  }
  if (family === 'witness') {
    return (
      <svg
        viewBox="0 0 24 24"
        className={cls}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      >
        <path d="M2.5 12s3.5-6.3 9.5-6.3 9.5 6.3 9.5 6.3-3.5 6.3-9.5 6.3S2.5 12 2.5 12z" />
        <circle cx="12" cy="12" r="2.8" fill="currentColor" stroke="none" />
      </svg>
    )
  }
  return <StarIcon className={cls} />
}

function StarIcon({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <path d="M12 2.8l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.1 6.4 20l1.1-6.2L3 9.4l6.2-.9z" />
    </svg>
  )
}

function LockIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-3"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      aria-hidden
    >
      <rect x="5" y="10.5" width="14" height="10" rx="2" fill="currentColor" stroke="none" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
    </svg>
  )
}
