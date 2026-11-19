import { useId, useRef, useState, type ChangeEvent, type ReactNode } from 'react'
import { focusRing } from '../deeds/deedUi'
import { BlurEditor } from './BlurEditor'
import type { MediaOptions } from './mediaApi'
import type { PhotoUploads, UploadItem } from './usePhotoUploads'

/**
 * The photo section of a form (photos canvas §1, M-FormPhotos; docs/M3-DESIGN.md §6.3):
 * "{n} of {limit}", 76 px thumbnails (96 px on desktop) with a remove ✕, Blur face / Edit blur
 * under each (deed photos only), a "Blurred" chip, and a dashed Add tile until the limit.
 * Uploading shows a spinner; a failed upload gets a red outline and Retry.
 */
export function PhotoPicker({
  uploads,
  options,
  existingCount = 0,
  existing,
  blur,
  help,
  title = 'Photos',
  optional = true,
}: {
  uploads: PhotoUploads
  options: MediaOptions
  /** Photos already attached (My deeds, self-log edit); they count toward the limit. */
  existingCount?: number
  /** The grid of already attached photos, shown under the heading. */
  existing?: ReactNode
  /** Offer the blur editor (deed photos; never on private self-log photos). */
  blur: boolean
  help: string
  title?: string
  optional?: boolean
}) {
  const headingId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const { items } = uploads
  const total = existingCount + items.length
  const limit = options.photos_per_owner
  const editingItem = items.find((item) => item.key === editing && item.previewUrl)

  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (files.length > 0) uploads.add(files)
  }

  const failedError = items.find((i) => i.status === 'failed')?.error ?? null
  const uploading = items.filter((i) => i.status === 'preparing' || i.status === 'uploading').length

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id={headingId} className="font-semibold text-slate-900">
          {title}
          {optional && (
            <>
              {' '}
              <span className="font-normal text-slate-600">(optional)</span>
            </>
          )}
        </h2>
        <span className="text-sm text-slate-600">
          {total} of {limit}
        </span>
      </div>
      {existing}
      <ul className="flex flex-wrap gap-2.5">
        {items.map((item, index) => (
          <Thumbnail
            key={item.key}
            item={item}
            label={`Photo ${existingCount + index + 1}`}
            blur={blur}
            onRemove={() => uploads.remove(item.key)}
            onRetry={() => uploads.retry(item.key)}
            onBlur={() => setEditing(item.key)}
          />
        ))}
        {total < limit && (
          <li>
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              aria-label="Add photo"
              className={`flex size-[76px] min-h-11 flex-col items-center justify-center gap-0.5 rounded-[10px] border-[1.5px] border-dashed border-slate-400 bg-white text-xs font-semibold text-slate-700 hover:bg-slate-50 sm:size-24 ${focusRing}`}
            >
              <svg
                aria-hidden
                viewBox="0 0 24 24"
                className="size-5.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
                <circle cx="12" cy="13" r="3.5" />
              </svg>
              Add
            </button>
          </li>
        )}
      </ul>
      <input
        ref={inputRef}
        type="file"
        accept={options.upload_allowed_types.join(',')}
        multiple
        onChange={onPick}
        className="hidden"
        tabIndex={-1}
        aria-hidden
        data-testid="photo-input"
      />
      <p role="status" className="sr-only">
        {uploading > 0
          ? uploading === 1
            ? 'Uploading 1 photo…'
            : `Uploading ${uploading} photos…`
          : ''}
      </p>
      {uploads.notice && (
        <p role="alert" className="text-sm font-medium text-red-700">
          {uploads.notice}
        </p>
      )}
      {failedError && (
        <p role="alert" className="text-sm font-medium text-red-700">
          {failedError}
        </p>
      )}
      <p className="text-sm text-slate-600">{help}</p>
      {editingItem?.previewUrl && (
        <BlurEditor
          imageUrl={editingItem.previewUrl}
          width={editingItem.width}
          height={editingItem.height}
          initial={editingItem.regions}
          maxRegions={options.blur_max_regions}
          onCancel={() => setEditing(null)}
          onDone={(regions) => {
            uploads.setRegions(editingItem.key, regions)
            setEditing(null)
          }}
        />
      )}
    </section>
  )
}

function Thumbnail({
  item,
  label,
  blur,
  onRemove,
  onRetry,
  onBlur,
}: {
  item: UploadItem
  label: string
  blur: boolean
  onRemove: () => void
  onRetry: () => void
  onBlur: () => void
}) {
  const failed = item.status === 'failed'
  const busy = item.status === 'preparing' || item.status === 'uploading'
  const blurred = item.regions.length > 0
  return (
    <li className="flex w-[76px] flex-col items-center sm:w-24">
      <div className="relative size-[76px] sm:size-24">
        <div
          className={`size-full overflow-hidden rounded-[10px] bg-slate-200 ${
            failed ? 'outline-2 outline-offset-1 outline-red-700' : ''
          }`}
        >
          {item.previewUrl && (
            <img src={item.previewUrl} alt={label} className="size-full object-cover" />
          )}
          {busy && (
            <span
              aria-hidden
              className="absolute inset-0 flex items-center justify-center rounded-[10px] bg-slate-900/35"
            >
              <span className="size-7 animate-spin rounded-full border-[3px] border-white/40 border-t-white" />
            </span>
          )}
          {blurred && !busy && (
            <span className="absolute bottom-1 left-1 rounded-full bg-slate-900/80 px-1.5 py-px text-[11px] font-semibold text-white">
              Blurred
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${label.toLowerCase()}`}
          className={`absolute -top-2.5 -right-2.5 flex size-11 items-center justify-center rounded-full ${focusRing}`}
        >
          <span className="flex size-6.5 items-center justify-center rounded-full bg-slate-900/75 text-white">
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              className="size-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </span>
        </button>
      </div>
      {failed ? (
        <button
          type="button"
          onClick={onRetry}
          aria-label={`Retry ${label.toLowerCase()}`}
          className={`min-h-11 px-0.5 text-[13px] font-semibold whitespace-nowrap text-red-700 ${focusRing}`}
        >
          Retry
        </button>
      ) : blur ? (
        <button
          type="button"
          onClick={onBlur}
          disabled={!item.previewUrl}
          aria-label={
            blurred
              ? `Edit blur on ${label.toLowerCase()}`
              : `Blur a face in ${label.toLowerCase()}`
          }
          className={`min-h-11 px-0.5 text-[13px] font-semibold whitespace-nowrap text-brand-700 disabled:opacity-50 ${focusRing}`}
        >
          {blurred ? 'Edit blur' : 'Blur face'}
        </button>
      ) : (
        <span className="min-h-2" />
      )}
    </li>
  )
}
