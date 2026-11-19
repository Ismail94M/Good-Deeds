import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ApiError, errorMessage } from '../../lib/api'
import type { DeedOut } from '../deeds/deedsApi'
import { focusRing, primaryButtonClass } from '../deeds/deedUi'
import { mediaApi, mediaKeys, type MediaOptions, type OwnerPhoto } from './mediaApi'
import { pollWhileProcessing, useMediaOptions } from './mediaQueries'
import { formatDeadline } from './mediaText'
import { markOutcomesSeen } from './outcomeSeen'
import { PhotoOutcomeBanner } from './PhotoOutcomeBanner'
import { PhotoPicker } from './PhotoPicker'
import { usePhotoUploads } from './usePhotoUploads'

const DEED_PHOTO_HELP =
  'Good deeds are meant to be shared proudly. If someone would rather not be shown, blur their face or remove the photo.'

/**
 * Photos on the owner view `/me/deeds/:id` (docs/M3-DESIGN.md §6.3 "My deeds detail"):
 * review outcomes (reporter only), a grid of up to 3 across with a ✕ where `can_remove`, and
 * Add photos inside the 24-hour window. The reporter removes their own photos at any time (Q30);
 * the doer of a verified deed removes any photo, for good (Q29).
 *
 * Existing photos and removal are shown even when `photos_enabled` is off, so people can always
 * take a photo down; only adding needs the switch.
 */
export function DeedPhotos({ deed, viewer }: { deed: DeedOut; viewer: 'doer' | 'reporter' }) {
  const enabled = viewer === 'reporter' || deed.status === 'verified'
  const query = useQuery({
    queryKey: mediaKeys.deed(deed.id),
    queryFn: () => mediaApi.deedPhotos(deed.id),
    enabled,
    retry: false,
    refetchInterval: (q) => pollWhileProcessing(q.state.data?.photos),
  })
  const media = useMediaOptions()
  const shown = viewer === 'reporter' ? query.data?.outcomes : undefined
  useEffect(() => {
    if (shown && shown.length > 0) markOutcomesSeen(deed.id, shown)
  }, [deed.id, shown])

  if (!enabled || !query.data) return null
  const data = query.data
  const options = media.data?.photos_enabled ? media.data : undefined
  const canAdd = viewer === 'reporter' && data.can_add && options !== undefined
  const outcomes = viewer === 'reporter' ? data.outcomes : []
  const doerName = deed.doer?.display_name ?? 'the doer'
  if (data.photos.length === 0 && !canAdd && outcomes.length === 0) return null

  return (
    <>
      {outcomes.map((outcome, index) => (
        <PhotoOutcomeBanner key={index} outcome={outcome} doerName={doerName} />
      ))}
      {(data.photos.length > 0 || canAdd) && (
        <section aria-labelledby="deed-photos-heading" className="flex flex-col gap-2.5">
          <h2 id="deed-photos-heading" className="font-semibold text-slate-900">
            Photos
          </h2>
          {data.photos.length > 0 && (
            <OwnerPhotoGrid
              photos={data.photos}
              confirmBody={
                viewer === 'doer'
                  ? `It will be removed from the feed, this deed and its share link. ${deed.reporter.display_name} can’t put it back. Your deed stays verified and your points don’t change.`
                  : 'It’s deleted for everyone.'
              }
              invalidate={mediaKeys.deed(deed.id)}
              onImageError={() => void query.refetch()}
            />
          )}
          {viewer === 'reporter' && (
            <p className="text-sm text-slate-600">
              {canAdd && data.add_until
                ? `You can add photos until ${formatDeadline(data.add_until)}. You can remove your photos at any time.`
                : 'You can remove your photos at any time.'}
            </p>
          )}
          {canAdd && options && (
            <AddDeedPhotos deedId={deed.id} options={options} existingCount={data.photos.length} />
          )}
        </section>
      )}
    </>
  )
}

function AddDeedPhotos({
  deedId,
  options,
  existingCount,
}: {
  deedId: string
  options: MediaOptions
  existingCount: number
}) {
  const queryClient = useQueryClient()
  const uploads = usePhotoUploads({
    purpose: 'deed',
    options,
    slots: options.photos_per_owner - existingCount,
  })
  const attach = useMutation({
    mutationFn: () => mediaApi.attachToDeed(deedId, { photos: uploads.attachable }),
    onSuccess: () => {
      uploads.clear()
      void queryClient.invalidateQueries({ queryKey: mediaKeys.deed(deedId) })
      void queryClient.invalidateQueries({ queryKey: ['feed'] })
    },
    onError: () => void queryClient.invalidateQueries({ queryKey: mediaKeys.deed(deedId) }),
  })
  const ready = uploads.attachable.length
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4">
      <PhotoPicker
        uploads={uploads}
        options={options}
        existingCount={existingCount}
        blur
        title="Add photos"
        optional={false}
        help={DEED_PHOTO_HELP}
      />
      {attach.isError && (
        <p role="alert" className="text-sm font-medium text-red-700">
          {attachErrorCopy(attach.error)}
        </p>
      )}
      {uploads.items.length > 0 && (
        <button
          type="button"
          onClick={() => attach.mutate()}
          disabled={ready === 0 || uploads.busy || attach.isPending}
          className={`${primaryButtonClass} self-start`}
        >
          {uploads.busy
            ? 'Uploading photos…'
            : attach.isPending
              ? 'Adding…'
              : ready === 1
                ? 'Add 1 photo to this deed'
                : `Add ${ready} photos to this deed`}
        </button>
      )}
    </div>
  )
}

function attachErrorCopy(error: unknown): string {
  const code = error instanceof ApiError ? error.code : null
  if (code === 'photo_window_closed') return 'The time to add photos to this deed has passed.'
  if (code === 'too_many_photos') return 'This deed already has the most photos allowed.'
  if (code === 'deed_not_editable') return 'Photos can’t be added to this deed any more.'
  return errorMessage(error)
}

/** A self-log's attached photos (private; owner only), with remove. */
export function SelfLogPhotoGrid({
  selfLogId,
  photos,
  onImageError,
}: {
  selfLogId: string
  photos: OwnerPhoto[]
  onImageError: () => void
}) {
  return (
    <OwnerPhotoGrid
      photos={photos}
      confirmBody="It’s deleted for good."
      invalidate={mediaKeys.selfLog(selfLogId)}
      onImageError={onImageError}
    />
  )
}

function OwnerPhotoGrid({
  photos,
  confirmBody,
  invalidate,
  onImageError,
}: {
  photos: OwnerPhoto[]
  confirmBody: string
  invalidate: readonly unknown[]
  onImageError: () => void
}) {
  const queryClient = useQueryClient()
  const [removing, setRemoving] = useState<OwnerPhoto | null>(null)
  // Signed URLs expire: refetch once when an image fails to load (§6.4).
  const refetched = useRef(false)
  const remove = useMutation({
    mutationFn: (photo: OwnerPhoto) => mediaApi.deletePhoto(photo.id),
    onSuccess: () => {
      setRemoving(null)
      void queryClient.invalidateQueries({ queryKey: invalidate })
      void queryClient.invalidateQueries({ queryKey: ['feed'] })
    },
  })
  const n = photos.length
  return (
    <>
      <ul className="grid grid-cols-3 gap-1">
        {photos.map((photo, index) => {
          const label = `Photo ${index + 1} of ${n}`
          return (
            <li key={photo.id} className="relative">
              <div className="flex h-[110px] items-center justify-center overflow-hidden rounded-[10px] bg-slate-100 text-center text-[13px] text-slate-600">
                {photo.status === 'ready' && photo.thumb_url ? (
                  <img
                    src={photo.thumb_url}
                    alt={label}
                    className="size-full object-cover"
                    onError={() => {
                      if (refetched.current) return
                      refetched.current = true
                      onImageError()
                    }}
                  />
                ) : photo.status === 'failed' ? (
                  <span className="px-2 text-red-700">
                    This photo couldn’t be prepared. Remove it and try again.
                  </span>
                ) : (
                  <span className="px-2">Preparing photo…</span>
                )}
                {photo.blurred_count > 0 && photo.status === 'ready' && (
                  <span className="absolute bottom-1.5 left-1.5 rounded-full bg-slate-900/80 px-1.5 py-px text-[11px] font-semibold text-white">
                    Blurred
                  </span>
                )}
              </div>
              {photo.can_remove && (
                <button
                  type="button"
                  onClick={() => {
                    remove.reset()
                    setRemoving(photo)
                  }}
                  aria-label={`Remove photo ${index + 1}`}
                  className={`absolute top-0 right-0 flex size-11 items-center justify-center rounded-full ${focusRing}`}
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
              )}
            </li>
          )
        })}
      </ul>
      {removing && (
        <ConfirmDialog
          title="Remove this photo?"
          body={confirmBody}
          busy={remove.isPending}
          error={remove.isError ? errorMessage(remove.error) : null}
          onDismiss={() => setRemoving(null)}
          actions={[
            { label: 'Remove photo', variant: 'danger', onClick: () => remove.mutate(removing) },
            {
              label: 'Keep it',
              variant: 'secondary',
              onClick: () => setRemoving(null),
              autoFocus: true,
            },
          ]}
        />
      )}
    </>
  )
}
