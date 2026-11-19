import { useQuery } from '@tanstack/react-query'
import { mediaApi, mediaKeys, type OwnerPhoto } from './mediaApi'

/**
 * `GET /media/options`. Photo controls render only when this loads and `photos_enabled` is true;
 * a failed load just hides them, so a deed or self-log can still be saved without photos.
 * Onboarded users only (§3.3 #1): pass `enabled = false` for anyone else.
 */
export function useMediaOptions(enabled = true) {
  return useQuery({
    queryKey: mediaKeys.options,
    enabled,
    queryFn: mediaApi.options,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

/** Poll while the worker prepares a photo (it picks queued rows up within seconds, §4.4). */
const PROCESSING_POLL_MS = 3000

export const pollWhileProcessing = (photos: OwnerPhoto[] | undefined) =>
  photos?.some((photo) => photo.status === 'processing') ? PROCESSING_POLL_MS : false

/** `GET /media/self-logs/{id}/photos`, polling while a photo is being prepared. */
export function useSelfLogPhotos(selfLogId: string | null) {
  return useQuery({
    queryKey: mediaKeys.selfLog(selfLogId ?? ''),
    queryFn: () => mediaApi.selfLogPhotos(selfLogId ?? ''),
    enabled: selfLogId !== null,
    retry: false,
    refetchInterval: (q) => pollWhileProcessing(q.state.data?.photos),
  })
}
