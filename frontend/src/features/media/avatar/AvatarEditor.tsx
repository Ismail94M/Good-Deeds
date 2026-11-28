import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../../lib/api'
import { Sheet } from '../../feed/feedUi'
import { putToStorage } from '../mediaApi'
import { ImageUnreadable, resizeImage } from '../resizeImage'
import { avatarApi, avatarKeys, type AvatarRejectReason, type MyAvatar } from './avatarApi'
import {
  AVATAR_MIN_SIDE,
  clampCrop,
  imageStyle,
  initialCrop,
  maxZoom,
  panBy,
  toApiCrop,
  zoomTo,
  type CropState,
  type ImageSize,
} from './cropGeometry'

/** Types a browser can decode and the API accepts (`upload_allowed_types`). */
export const AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp']
/** Bigger originals are refused before decoding; the upload itself is resized to ≤ 2048 px. */
export const AVATAR_RAW_MAX_BYTES = 25 * 1024 * 1024
const POLL_MS = 1500
const POLL_LIMIT = 40

const timeFormat = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
  timeZone: 'Asia/Kolkata',
})

export function formatWhen(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? 'a little later' : timeFormat.format(date)
}

export const COPY = {
  unsupported: 'This file type isn’t supported. Choose a JPEG, PNG or WebP photo.',
  tooLarge: 'This photo is too large. Choose a smaller one.',
  tooSmall: `This photo is too small: pick one at least ${AVATAR_MIN_SIDE} × ${AVATAR_MIN_SIDE}.`,
  tooMany: 'You’ve changed your photo a lot today; try tomorrow.',
  failed: 'Your photo didn’t upload. Check your connection and try again.',
  /** The upload arrived with the wrong size: not a connection problem (SEC6 follow-up). */
  mismatch: 'That upload didn’t finish correctly. Please try again.',
  disabled: 'Profile photos are switched off for now.',
  onboarding: 'Finish setting up your account (verify your phone) to add a profile photo.',
}

const REJECT_COPY: Partial<Record<AvatarRejectReason, string>> = {
  unsupported_type: COPY.unsupported,
  too_large: COPY.tooLarge,
  too_many_pixels: COPY.tooLarge,
  animated: 'Moving pictures can’t be used. Choose a still photo.',
  corrupt: 'We couldn’t read this photo. Try another one.',
  size_mismatch: COPY.mismatch,
}

/** The message for an error from any step of saving (P1, P2, PUT, complete). */
export function avatarErrorCopy(error: unknown): string {
  if (!(error instanceof ApiError)) return COPY.failed
  switch (error.code) {
    case 'unsupported_type':
      return COPY.unsupported
    case 'too_large':
      return COPY.tooLarge
    case 'image_too_small':
      return COPY.tooSmall
    case 'crop_invalid':
      return 'The crop doesn’t fit the photo. Please try again.'
    case 'avatars_disabled':
      return COPY.disabled
    case 'onboarding_required':
      return COPY.onboarding
    case 'size_mismatch':
      return COPY.mismatch
    case 'cooldown': {
      const at = error.extra.available_at
      return `You can add a new photo after ${typeof at === 'string' ? formatWhen(at) : 'a little while'}.`
    }
  }
  if (error.status === 413) return COPY.tooLarge
  if (error.status === 429) return COPY.tooMany
  return error.detail || COPY.failed
}

type Picked = { blob: Blob; url: string; size: ImageSize }
type Phase = 'choose' | 'crop' | 'uploading' | 'processing' | 'done'

/** True on touch-first devices, where "Take photo" opens the front camera. */
function hasCamera(): boolean {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(pointer: coarse)').matches)
}

/**
 * The profile photo editor (§6.3, D116): a bottom sheet on phones and a dialog on desktop.
 * Choose or take a photo → crop it to a square (drag, pinch, the zoom slider, or arrow keys and
 * +/−) with a live round preview at three sizes → Save (upload, then the server crops and
 * processes it). Replace and remove work from the same sheet. Pixels outside the square are cut
 * away on the server, so they are never stored.
 */
export function AvatarEditor({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const mine = useQuery({ queryKey: avatarKeys.mine, queryFn: avatarApi.mine, retry: false })
  const [phase, setPhase] = useState<Phase>('choose')
  const [picked, setPicked] = useState<Picked | null>(null)
  const [crop, setCrop] = useState<CropState>({ cx: 0.5, cy: 0.5, zoom: 1 })
  const [error, setError] = useState<string | null>(null)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [removing, setRemoving] = useState(false)
  const chooseInput = useRef<HTMLInputElement>(null)
  const cameraInput = useRef<HTMLInputElement>(null)
  const cancelled = useRef(false)
  const [openedAt] = useState(() => Date.now())

  useEffect(() => {
    cancelled.current = false
    return () => {
      cancelled.current = true
    }
  }, [])
  useEffect(() => () => void (picked && URL.revokeObjectURL(picked.url)), [picked])

  const current = mine.data?.current ?? null
  const removed = mine.data?.removed_notice ?? null
  const uploadAfter = removed?.upload_after ?? null
  const waiting = uploadAfter !== null && Date.parse(uploadAfter) > openedAt

  async function onFile(file: File | undefined) {
    if (!file) return
    setError(null)
    if (file.type && !AVATAR_TYPES.includes(file.type)) return setError(COPY.unsupported)
    if (file.size > AVATAR_RAW_MAX_BYTES) return setError(COPY.tooLarge)
    try {
      const resized = await resizeImage(file)
      if (Math.min(resized.width, resized.height) < AVATAR_MIN_SIDE) return setError(COPY.tooSmall)
      const size = { width: resized.width, height: resized.height }
      setPicked({ blob: resized.blob, url: URL.createObjectURL(resized.blob), size })
      setCrop(initialCrop(size))
      setPhase('crop')
    } catch (err) {
      setError(err instanceof ImageUnreadable ? COPY.unsupported : COPY.failed)
    }
  }

  async function save() {
    if (!picked) return
    setError(null)
    setPhase('uploading')
    try {
      const ticket = await avatarApi.createUpload('image/jpeg', picked.blob.size)
      await putToStorage(ticket, picked.blob)
      await avatarApi.completeUpload(ticket.photo_id)
      await avatarApi.setMine(ticket.photo_id, toApiCrop(crop, picked.size))
      if (cancelled.current) return
      setPhase('processing')
      await waitUntilReady()
    } catch (err) {
      if (cancelled.current) return
      setError(avatarErrorCopy(err))
      setPhase('crop')
    }
  }

  async function waitUntilReady() {
    for (let i = 0; i < POLL_LIMIT; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, i === 0 ? 300 : POLL_MS))
      if (cancelled.current) return
      let state: MyAvatar
      try {
        state = await avatarApi.mine()
      } catch {
        continue
      }
      queryClient.setQueryData(avatarKeys.mine, state)
      const pending = state.pending
      if (!pending) return finish()
      if (pending.status !== 'processing') {
        const reason = pending.reject_reason as AvatarRejectReason | null | undefined
        // Finished during the wait after an admin removal (Q95, SEC6-L3): say when to retry.
        const extra =
          reason === 'cooldown' ? { available_at: state.removed_notice?.upload_after } : {}
        const text = (reason && REJECT_COPY[reason]) || COPY.failed
        throw new ApiError(422, text, reason ?? null, {}, extra)
      }
    }
    // Still working: it will appear by itself once ready.
    finish()
  }

  function finish() {
    void queryClient.invalidateQueries({ queryKey: ['me'] })
    setPhase('done')
  }

  async function remove() {
    setRemoving(true)
    setError(null)
    try {
      await avatarApi.removeMine()
      void queryClient.invalidateQueries({ queryKey: ['me'] })
      onClose()
    } catch (err) {
      setError(avatarErrorCopy(err))
      setRemoving(false)
      setConfirmRemove(false)
    }
  }

  const busy = phase === 'uploading' || phase === 'processing'
  const title =
    phase === 'crop' || busy ? 'Crop your photo' : current ? 'Profile photo' : 'Add a profile photo'

  return (
    <>
      <input
        ref={chooseInput}
        type="file"
        accept={AVATAR_TYPES.join(',')}
        hidden
        data-testid="avatar-file"
        onChange={(event) => {
          void onFile(event.target.files?.[0])
          event.target.value = ''
        }}
      />
      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="user"
        hidden
        onChange={(event) => {
          void onFile(event.target.files?.[0])
          event.target.value = ''
        }}
      />
      <Sheet title={title} onClose={busy ? () => {} : onClose}>
        {phase === 'done' ? (
          <div className="flex flex-col gap-3">
            <p role="status" className="text-slate-700">
              Your profile photo is updated.
            </p>
            <button type="button" onClick={onClose} className={primary}>
              Done
            </button>
          </div>
        ) : phase === 'choose' ? (
          <div className="flex flex-col gap-3">
            <p className="text-slate-700">
              Add a profile photo. Use a photo of you, or any picture without other people. No
              children.
            </p>
            {removed && (
              <p className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm text-amber-900">
                Your last profile picture was removed: {removed.reason_label}. Please check our{' '}
                <Link to="/photo-guidelines" className="font-semibold underline">
                  photo guidelines
                </Link>
                .
                {waiting &&
                  uploadAfter &&
                  ` You can add a new photo after ${formatWhen(uploadAfter)}.`}
              </p>
            )}
            {error && (
              <p role="alert" className="text-sm font-medium text-red-700">
                {error}
              </p>
            )}
            {hasCamera() && (
              <button
                type="button"
                disabled={waiting}
                onClick={() => cameraInput.current?.click()}
                className={primary}
              >
                Take photo
              </button>
            )}
            <button
              type="button"
              disabled={waiting}
              onClick={() => chooseInput.current?.click()}
              className={hasCamera() ? secondary : primary}
            >
              {current ? 'Choose a new photo' : 'Choose photo'}
            </button>
            {current &&
              (confirmRemove ? (
                <div className="flex flex-col gap-2 rounded-xl border border-slate-200 p-3">
                  <p className="text-[15px] text-slate-800">
                    Remove your profile photo? It’s deleted straight away and everyone sees your
                    initials instead.
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setConfirmRemove(false)}
                      disabled={removing}
                      className={secondary}
                    >
                      Keep it
                    </button>
                    <button
                      type="button"
                      onClick={() => void remove()}
                      disabled={removing}
                      className={danger}
                    >
                      {removing ? 'Removing…' : 'Remove'}
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" onClick={() => setConfirmRemove(true)} className={dangerText}>
                  Remove photo
                </button>
              ))}
            <Link
              to="/photo-guidelines"
              className="self-center text-sm font-medium text-brand-700 underline underline-offset-2"
            >
              Photo guidelines
            </Link>
            <button type="button" onClick={onClose} className={secondary}>
              Cancel
            </button>
          </div>
        ) : (
          picked && (
            <div className="flex flex-col gap-3">
              <CropFrame image={picked} crop={crop} onChange={setCrop} disabled={busy} />
              <label className="flex items-center gap-3 text-sm font-medium text-slate-700">
                <span>Zoom</span>
                <input
                  type="range"
                  min={1}
                  max={maxZoom(picked.size)}
                  step={0.01}
                  value={crop.zoom}
                  disabled={busy || maxZoom(picked.size) <= 1}
                  onChange={(event) =>
                    setCrop(zoomTo(crop, picked.size, Number(event.target.value)))
                  }
                  className="min-h-11 grow accent-brand-700"
                />
              </label>
              <div className="flex items-center gap-4" aria-label="Preview" role="group">
                <span className="text-sm text-slate-600">Preview</span>
                {[96, 48, 32].map((px) => (
                  <Preview key={px} image={picked} crop={crop} px={px} />
                ))}
              </div>
              {error && (
                <p role="alert" className="text-sm font-medium text-red-700">
                  {error}
                </p>
              )}
              {busy && (
                <p role="status" className="text-sm text-slate-700">
                  {phase === 'uploading' ? 'Uploading…' : 'Processing…'}
                </p>
              )}
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setError(null)
                    setPhase('choose')
                  }}
                  className={secondary}
                >
                  Back
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void save()}
                  className={primary}
                >
                  {busy ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          )
        )}
      </Sheet>
    </>
  )
}

const ring =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'
const base = `inline-flex min-h-12 w-full items-center justify-center rounded-lg px-4 text-base font-semibold disabled:cursor-not-allowed disabled:opacity-60 ${ring}`
const primary = `${base} bg-brand-700 text-white hover:bg-brand-900`
const secondary = `${base} border border-slate-200 bg-white text-slate-800 hover:bg-slate-50`
const danger = `${base} bg-red-700 text-white hover:bg-red-800`
const dangerText = `inline-flex min-h-11 items-center justify-center self-center rounded px-2 font-semibold text-red-700 hover:text-red-800 ${ring}`

const KEY_STEP_PX = 12
const ZOOM_STEP = 0.1

/**
 * The square crop area: drag (mouse or one finger), pinch (two fingers), and the keyboard
 * (arrows move, +/− zoom). The round mask shows what will appear in the app.
 */
function CropFrame({
  image,
  crop,
  onChange,
  disabled,
}: {
  image: Picked
  crop: CropState
  onChange: (crop: CropState) => void
  disabled: boolean
}) {
  const frame = useRef<HTMLDivElement>(null)
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const pinch = useRef<{ distance: number; zoom: number } | null>(null)
  const latest = useRef(crop)
  useEffect(() => {
    latest.current = crop
  }, [crop])

  const frameSize = () => frame.current?.getBoundingClientRect().width || 280
  const distance = () => {
    const [a, b] = [...pointers.current.values()]
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0
  }

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (disabled) return
    event.currentTarget.setPointerCapture?.(event.pointerId)
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    if (pointers.current.size === 2)
      pinch.current = { distance: distance(), zoom: latest.current.zoom }
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const last = pointers.current.get(event.pointerId)
    if (!last || disabled) return
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
    if (pointers.current.size >= 2 && pinch.current && pinch.current.distance > 0) {
      const zoom = pinch.current.zoom * (distance() / pinch.current.distance)
      onChange(zoomTo(latest.current, image.size, zoom))
      return
    }
    onChange(
      panBy(
        latest.current,
        image.size,
        event.clientX - last.x,
        event.clientY - last.y,
        frameSize(),
      ),
    )
  }
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId)
    if (pointers.current.size < 2) pinch.current = null
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return
    const step = event.shiftKey ? KEY_STEP_PX * 4 : KEY_STEP_PX
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [step, 0],
      ArrowRight: [-step, 0],
      ArrowUp: [0, step],
      ArrowDown: [0, -step],
    }
    let next: CropState | null = null
    if (moves[event.key]) {
      const [dx, dy] = moves[event.key]
      // Arrow keys move the square (not the picture): the opposite of a drag.
      next = panBy(crop, image.size, dx, dy, frameSize())
    } else if (event.key === '+' || event.key === '=') {
      next = zoomTo(crop, image.size, crop.zoom + ZOOM_STEP)
    } else if (event.key === '-' || event.key === '_') {
      next = zoomTo(crop, image.size, crop.zoom - ZOOM_STEP)
    }
    if (!next) return
    event.preventDefault()
    onChange(clampCrop(next, image.size))
  }

  return (
    <div
      ref={frame}
      tabIndex={0}
      role="group"
      aria-label="Crop area. Drag to move the photo. Arrow keys move it, plus and minus zoom."
      aria-roledescription="crop area"
      data-testid="crop-frame"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
      className={`relative mx-auto aspect-square w-full max-w-[320px] cursor-grab touch-none overflow-hidden rounded-xl bg-slate-900 select-none active:cursor-grabbing ${ring}`}
    >
      <img
        src={image.url}
        alt="Your photo, being cropped"
        draggable={false}
        className="pointer-events-none absolute max-w-none"
        style={imageStyle(crop, image.size)}
      />
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-full shadow-[0_0_0_9999px_rgba(15,23,42,0.55)] ring-2 ring-white/80"
      />
    </div>
  )
}

function Preview({ image, crop, px }: { image: Picked; crop: CropState; px: number }) {
  return (
    <span
      aria-hidden
      data-testid="avatar-preview"
      className="relative shrink-0 overflow-hidden rounded-full bg-slate-200 ring-1 ring-slate-200"
      style={{ width: px, height: px }}
    >
      <img
        src={image.url}
        alt=""
        draggable={false}
        className="absolute max-w-none"
        style={imageStyle(crop, image.size)}
      />
    </span>
  )
}
