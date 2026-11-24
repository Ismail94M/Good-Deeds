import { useMutation, useQueryClient } from '@tanstack/react-query'
import { mediaApi } from '../media/mediaApi'
import { patchPosts } from './postActions'

/**
 * The doer removes a photo from their verified deed (Q29, `DELETE /media/photos/{id}`). On
 * success the photo disappears from every cached copy of the post at once, with no placeholder
 * (Q33), and the owner photo list in My deeds is refreshed.
 */
export function useRemovePhoto(deedId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (photoId: string) => mediaApi.deletePhoto(photoId),
    onSuccess: (_data, photoId) => {
      patchPosts(
        queryClient,
        (post) => post.deed_id === deedId,
        (post) => ({ photos: post.photos.filter((photo) => photo.id !== photoId) }),
      )
      void queryClient.invalidateQueries({ queryKey: ['feed'], refetchType: 'none' })
      void queryClient.invalidateQueries({ queryKey: ['media', 'deed', deedId] })
    },
  })
}
