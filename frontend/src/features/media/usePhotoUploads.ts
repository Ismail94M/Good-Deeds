import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../../lib/api'
import {
  mediaApi,
  putToStorage,
  type AttachPhoto,
  type BlurRegion,
  type MediaOptions,
  type UploadPurpose,
} from './mediaApi'
import { fileRefusedCopy } from './mediaText'
import { ImageUnreadable, OUTPUT_TYPE, resizeImage } from './resizeImage'

export type UploadStatus = 'preparing' | 'uploading' | 'uploaded' | 'failed'

/** One photo picked on a form, before it is attached to a deed or self-log. */
export type UploadItem = {
  key: string
  status: UploadStatus
  /** Object URL of the resized image (local only). Null while preparing. */
  previewUrl: string | null
  /** Size of the resized, orientation-corrected image: the frame blur regions refer to. */
  width: number
  height: number
  photoId: string | null
  /** Why the upload failed (with Retry). */
  error: string | null
  regions: BlurRegion[]
}

const FAILED = 'This photo didn’t upload. Check your connection and try again.'
const TOO_MANY_PENDING = 'You have too many photos waiting. Send or remove some, then try again.'
const RATE_LIMITED = 'You’re adding photos very fast. Wait a minute, then try again.'

let nextKey = 0

const isBusy = (items: UploadItem[]) =>
  items.some((item) => item.status === 'preparing' || item.status === 'uploading')

function toAttach(items: UploadItem[], purpose: UploadPurpose): AttachPhoto[] {
  return items
    .filter((item) => item.status === 'uploaded' && item.photoId)
    .map((item) => ({
      photo_id: item.photoId as string,
      blur_regions: purpose === 'deed' ? item.regions : [],
    }))
}

/** Unattached uploads expire on the server anyway (§4.8), so a failed discard is ignored. */
function discard(photoId: string) {
  void mediaApi.discardUpload(photoId).catch(() => {})
}

/**
 * Pick → resize → `POST /media/uploads` → PUT to storage → `/complete` for each photo, with
 * Retry and discard (docs/M3-DESIGN.md §4.1, §6.3). Photos are attached by the caller after its
 * own save, with `attachable`. A refused file (type or size) never becomes a thumbnail: it sets
 * `notice` instead.
 *
 * The PUT uses `putToStorage` (plain `fetch` with only the signed headers, no referrer) rather
 * than XHR, so there is no byte progress: thumbnails show a spinner while uploading.
 */
export function usePhotoUploads({
  purpose,
  options,
  slots,
}: {
  purpose: UploadPurpose
  options: MediaOptions | undefined
  /** How many more photos may be added (the owner limit minus photos already attached). */
  slots: number
}) {
  const [items, setItems] = useState<UploadItem[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const itemsRef = useRef<UploadItem[]>([])
  const blobs = useRef(new Map<string, Blob>())
  const putDone = useRef(new Set<string>())
  const optionsRef = useRef(options)
  useEffect(() => {
    optionsRef.current = options
  }, [options])

  const waiters = useRef<(() => void)[]>([])

  const commit = useCallback((next: (current: UploadItem[]) => UploadItem[]) => {
    itemsRef.current = next(itemsRef.current)
    setItems(itemsRef.current)
    if (!isBusy(itemsRef.current) && waiters.current.length > 0) {
      const ready = waiters.current
      waiters.current = []
      for (const resolve of ready) resolve()
    }
  }, [])
  const patch = useCallback(
    (key: string, change: Partial<UploadItem>) =>
      commit((current) =>
        current.map((item) => (item.key === key ? { ...item, ...change } : item)),
      ),
    [commit],
  )
  const find = (key: string) => itemsRef.current.find((item) => item.key === key)
  const alive = (key: string) => find(key) !== undefined

  const removeItem = useCallback(
    (key: string) => {
      const item = itemsRef.current.find((i) => i.key === key)
      if (!item) return
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
      blobs.current.delete(key)
      putDone.current.delete(key)
      commit((current) => current.filter((i) => i.key !== key))
      return item
    },
    [commit],
  )

  const upload = useCallback(
    async (key: string) => {
      const blob = blobs.current.get(key)
      if (!blob) return
      patch(key, { status: 'uploading', error: null })
      try {
        let photoId = find(key)?.photoId ?? null
        if (!photoId || !putDone.current.has(key)) {
          if (photoId) discard(photoId)
          const ticket = await mediaApi.createUpload({
            purpose,
            content_type: OUTPUT_TYPE,
            size_bytes: blob.size,
          })
          photoId = ticket.photo_id
          if (!alive(key)) {
            discard(photoId)
            return
          }
          patch(key, { photoId })
          await putToStorage(ticket, blob)
          putDone.current.add(key)
        }
        await mediaApi.completeUpload(photoId)
        if (!alive(key)) {
          discard(photoId)
          return
        }
        patch(key, { status: 'uploaded' })
      } catch (error) {
        if (!alive(key)) return
        const code = error instanceof ApiError ? error.code : null
        if ((code === 'unsupported_type' || code === 'too_large') && optionsRef.current) {
          removeItem(key)
          setNotice(fileRefusedCopy(optionsRef.current))
          return
        }
        if (code === 'upload_missing' || code === 'size_mismatch') putDone.current.delete(key)
        patch(key, {
          status: 'failed',
          error:
            code === 'too_many_pending_uploads'
              ? TOO_MANY_PENDING
              : code === 'rate_limited'
                ? RATE_LIMITED
                : FAILED,
        })
      }
    },
    // `find` and `alive` only read refs.
    [patch, purpose, removeItem],
  )

  const prepare = useCallback(
    async (key: string, file: File) => {
      const opts = optionsRef.current
      if (!opts) return
      if (!opts.upload_allowed_types.includes(file.type)) {
        removeItem(key)
        setNotice(fileRefusedCopy(opts))
        return
      }
      let resized
      try {
        resized = await resizeImage(file)
      } catch (error) {
        if (!alive(key)) return
        removeItem(key)
        setNotice(
          error instanceof ImageUnreadable
            ? fileRefusedCopy(opts)
            : 'That photo couldn’t be prepared. Try another one.',
        )
        return
      }
      if (!alive(key)) return
      if (resized.blob.size > opts.upload_max_bytes) {
        removeItem(key)
        setNotice(fileRefusedCopy(opts))
        return
      }
      blobs.current.set(key, resized.blob)
      patch(key, {
        previewUrl: URL.createObjectURL(resized.blob),
        width: resized.width,
        height: resized.height,
      })
      await upload(key)
    },
    [patch, removeItem, upload],
  )

  /** Add picked files, up to the free slots; extra files are ignored with a notice. */
  const add = useCallback(
    (files: File[]) => {
      setNotice(null)
      const free = Math.max(0, slots - itemsRef.current.length)
      const taken = files.slice(0, free)
      if (files.length > free) {
        setNotice(
          free === 0
            ? 'You’ve added the most photos allowed.'
            : `Only ${free === 1 ? '1 more photo fits' : `${free} more photos fit`}, so we added the first ${free === 1 ? 'one' : free}.`,
        )
      }
      const added: [string, File][] = taken.map((file) => [`u${++nextKey}`, file])
      commit((current) => [
        ...current,
        ...added.map(([key]): UploadItem => ({
          key,
          status: 'preparing',
          previewUrl: null,
          width: 0,
          height: 0,
          photoId: null,
          error: null,
          regions: [],
        })),
      ])
      for (const [key, file] of added) void prepare(key, file)
    },
    [commit, prepare, slots],
  )

  /** Remove a picked photo; its staged upload is discarded. */
  const remove = useCallback(
    (key: string) => {
      setNotice(null)
      const item = removeItem(key)
      if (item?.photoId) discard(item.photoId)
    },
    [removeItem],
  )

  const retry = useCallback((key: string) => void upload(key), [upload])

  const setRegions = useCallback(
    (key: string, regions: BlurRegion[]) => patch(key, { regions }),
    [patch],
  )

  /** Forget everything after a successful attach (nothing is discarded on the server). */
  const clear = useCallback(() => {
    for (const item of itemsRef.current) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
    blobs.current.clear()
    putDone.current.clear()
    commit(() => [])
    setNotice(null)
  }, [commit])

  useEffect(
    () => () => {
      for (const item of itemsRef.current) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
    },
    [],
  )

  /** Resolves once no photo is preparing or uploading (Send waits for this, §6.3). */
  const settled = useCallback(
    () =>
      new Promise<void>((resolve) => {
        if (!isBusy(itemsRef.current)) resolve()
        else waiters.current.push(resolve)
      }),
    [],
  )

  /** The current photos to attach and how many failed, read after `settled()`. */
  const snapshot = useCallback(
    () => ({
      attach: toAttach(itemsRef.current, purpose),
      failed: itemsRef.current.filter((item) => item.status === 'failed').length,
    }),
    [purpose],
  )

  const busy = isBusy(items)
  const attachable = useMemo(() => toAttach(items, purpose), [items, purpose])
  const failedCount = items.filter((item) => item.status === 'failed').length

  return {
    items,
    notice,
    busy,
    attachable,
    failedCount,
    add,
    remove,
    retry,
    setRegions,
    clear,
    settled,
    snapshot,
  }
}

export type PhotoUploads = ReturnType<typeof usePhotoUploads>
