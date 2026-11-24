import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { focusRing } from '../deeds/deedUi'
import {
  clampCircle,
  fromRegion,
  MOVE_STEP,
  MOVE_STEP_LARGE,
  newCircle,
  SIZE_STEP,
  toRegion,
  type Circle,
} from './blurGeometry'
import type { BlurRegion } from './mediaApi'

type Drag = {
  index: number
  mode: 'move' | 'resize'
  pointerId: number
  startX: number
  startY: number
  start: Circle
}

const button = `inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 text-[15px] font-semibold text-slate-800 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`

/**
 * Full-screen blur editor (photos canvas, M-BlurEditor; docs/M3-DESIGN.md §6.3). Circles are a
 * live preview (CSS backdrop blur) over the local, already resized photo; nothing unblurred is
 * uploaded anywhere but staging. Each circle drags with a pointer and resizes from its corner
 * handle; with focus, arrow keys move it (Shift for bigger steps) and + / − change its size.
 * Cancel discards; Done returns the regions as fractions of the image (§4.5).
 */
export function BlurEditor({
  imageUrl,
  width,
  height,
  initial,
  maxRegions,
  onCancel,
  onDone,
}: {
  imageUrl: string
  width: number
  height: number
  initial: BlurRegion[]
  maxRegions: number
  onCancel: () => void
  onDone: (regions: BlurRegion[]) => void
}) {
  const titleId = useId()
  const helpId = useId()
  const stageRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const [circles, setCircles] = useState<Circle[]>(() =>
    initial.length > 0
      ? initial.map((region) => fromRegion(region, width, height))
      : [newCircle(width, height)],
  )
  const [selected, setSelected] = useState<number | null>(0)
  /** A circle just added: focused as soon as its button mounts. */
  const pendingFocus = useRef<number | null>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    dialogRef.current?.querySelector<HTMLElement>('h2')?.focus()
    return () => opener?.focus()
  }, [])

  const update = (index: number, circle: Circle) =>
    setCircles((current) =>
      current.map((c, i) => (i === index ? clampCircle(circle, width, height) : c)),
    )

  const add = () => {
    if (circles.length >= maxRegions) return
    setCircles((current) => [...current, newCircle(width, height)])
    setSelected(circles.length)
    pendingFocus.current = circles.length
  }

  const removeSelected = () => {
    if (selected === null) return
    setCircles((current) => current.filter((_, i) => i !== selected))
    setSelected(null)
  }

  const startDrag = (event: ReactPointerEvent, index: number, mode: Drag['mode']) => {
    event.preventDefault()
    event.stopPropagation()
    setSelected(index)
    ;(event.currentTarget as Element).setPointerCapture?.(event.pointerId)
    drag.current = {
      index,
      mode,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      start: circles[index],
    }
  }

  const onPointerMove = (event: ReactPointerEvent) => {
    const active = drag.current
    const stage = stageRef.current
    if (!active || !stage || event.pointerId !== active.pointerId) return
    const rect = stage.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return
    const dx = event.clientX - active.startX
    const dy = event.clientY - active.startY
    if (active.mode === 'move') {
      update(active.index, {
        ...active.start,
        cx: active.start.cx + dx / rect.width,
        cy: active.start.cy + dy / rect.height,
      })
    } else {
      update(active.index, {
        ...active.start,
        d: active.start.d + (dx + dy) / Math.min(rect.width, rect.height),
      })
    }
  }

  const endDrag = () => {
    drag.current = null
  }

  const onCircleKey = (event: KeyboardEvent, index: number) => {
    const circle = circles[index]
    const step = event.shiftKey ? MOVE_STEP_LARGE : MOVE_STEP
    let next: Circle | null = null
    if (event.key === 'ArrowLeft') next = { ...circle, cx: circle.cx - step }
    else if (event.key === 'ArrowRight') next = { ...circle, cx: circle.cx + step }
    else if (event.key === 'ArrowUp') next = { ...circle, cy: circle.cy - step }
    else if (event.key === 'ArrowDown') next = { ...circle, cy: circle.cy + step }
    else if (event.key === '+' || event.key === '=') next = { ...circle, d: circle.d + SIZE_STEP }
    else if (event.key === '-' || event.key === '−' || event.key === '_') {
      next = { ...circle, d: circle.d - SIZE_STEP }
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault()
      setCircles((current) => current.filter((_, i) => i !== index))
      setSelected(null)
      return
    }
    if (next) {
      event.preventDefault()
      update(index, next)
    }
  }

  const onDialogKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onCancel()
    }
  }

  const count = circles.length
  const ratio = width / height

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onKeyDown={onDialogKey}
      className="fixed inset-0 z-50 overflow-y-auto bg-canvas"
    >
      <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4 px-4 pt-5 pb-8 sm:px-6 sm:pt-8">
        <h2
          id={titleId}
          tabIndex={-1}
          className="text-[26px] leading-tight font-bold tracking-tight text-slate-900 outline-none sm:text-[32px]"
        >
          Blur a face <span className="font-medium text-slate-600">(optional)</span>
        </h2>
        <p id={helpId} className="text-slate-700">
          Drag the circle over the face of anyone who’d rather not be shown. Everyone else stays
          exactly as they are.
        </p>
        <div
          ref={stageRef}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          className="relative mx-auto touch-none overflow-hidden rounded-[14px] bg-slate-200 select-none"
          style={{
            aspectRatio: `${width} / ${height}`,
            width: `min(100%, calc(60svh * ${ratio}))`,
          }}
        >
          <img
            src={imageUrl}
            alt="The photo you’re blurring"
            draggable={false}
            className="pointer-events-none absolute inset-0 size-full"
          />
          {circles.map((circle, index) => {
            const region = toRegion(circle, width, height)
            const isSelected = selected === index
            return (
              <div
                key={index}
                className="absolute"
                style={{
                  left: `${region.x * 100}%`,
                  top: `${region.y * 100}%`,
                  width: `${region.w * 100}%`,
                  height: `${region.h * 100}%`,
                }}
              >
                <button
                  type="button"
                  ref={(el) => {
                    if (el && pendingFocus.current === index) {
                      pendingFocus.current = null
                      el.focus()
                    }
                  }}
                  aria-label={`Blur ${index + 1} of ${count}`}
                  aria-describedby={`${helpId}-keys`}
                  aria-pressed={isSelected}
                  onFocus={() => setSelected(index)}
                  onPointerDown={(event) => startDrag(event, index, 'move')}
                  onKeyDown={(event) => onCircleKey(event, index)}
                  className={`absolute inset-0 cursor-move rounded-full border-[3px] border-white bg-white/10 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-brand-700 ${
                    isSelected
                      ? 'shadow-[0_0_0_2px_#3659e3,0_6px_16px_rgba(15,23,42,0.25)]'
                      : 'shadow-[0_2px_8px_rgba(15,23,42,0.3)]'
                  }`}
                  style={{ backdropFilter: 'blur(14px)', WebkitBackdropFilter: 'blur(14px)' }}
                />
                <span
                  aria-hidden
                  data-testid={`blur-resize-${index}`}
                  onPointerDown={(event) => startDrag(event, index, 'resize')}
                  className="absolute flex size-11 -translate-x-1/2 -translate-y-1/2 cursor-nwse-resize items-center justify-center"
                  style={{ left: '85.4%', top: '85.4%' }}
                >
                  <span
                    className={`size-4 rounded-full border-2 border-white shadow ${
                      isSelected ? 'bg-brand-700' : 'bg-slate-700'
                    }`}
                  />
                </span>
              </div>
            )
          })}
        </div>
        <p id={`${helpId}-keys`} className="text-sm text-[#586579]">
          Drag a circle to move it and its corner dot to resize it. With the keyboard, arrow keys
          move it and + or − change its size.
        </p>
        <p role="status" className="sr-only">
          {count === 0 ? 'No blurs on this photo.' : count === 1 ? '1 blur.' : `${count} blurs.`}
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={add} disabled={count >= maxRegions} className={button}>
            Add a blur
          </button>
          {selected !== null && selected < count && (
            <button type="button" onClick={removeSelected} className={button}>
              Remove this blur
            </button>
          )}
        </div>
        <p className="rounded-[10px] bg-slate-100 px-3 py-2.5 text-sm text-slate-700">
          Only the blurred version is posted. You can change this until you send the report.
        </p>
        <div className="grid grid-cols-2 gap-2.5">
          <button
            type="button"
            onClick={onCancel}
            className={`min-h-13 rounded-lg border border-slate-200 bg-white text-base font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onDone(circles.map((c) => toRegion(c, width, height)))}
            className={`min-h-13 rounded-lg bg-brand-700 text-base font-semibold text-white hover:bg-brand-900 ${focusRing}`}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}
