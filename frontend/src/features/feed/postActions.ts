/**
 * Optimistic like, save and follow (M3-DESIGN §6.3): the change shows at once in every cached
 * copy of the post (feed tabs, deed page, Saved, profiles), is corrected from the server's answer,
 * and rolls back on error. Lists are marked stale rather than refetched at once, so a card the
 * user just unsaved or unfollowed doesn't jump away under their finger.
 */

import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import {
  feedApi,
  feedKeys,
  type ConnectionOut,
  type Page,
  type PostOut,
  type SocialOut,
} from './feedApi'

type InfinitePages<T> = { pages: Page<T>[]; pageParams: unknown[] }

function isPost(value: unknown): value is PostOut {
  return typeof value === 'object' && value !== null && 'deed_id' in value && 'liked' in value
}

function isInfinite(value: unknown): value is InfinitePages<unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    Array.isArray((value as { pages?: unknown }).pages)
  )
}

/** Apply `change` to every cached post (list item or deed detail) that `match`es. */
export function patchPosts(
  queryClient: QueryClient,
  match: (post: PostOut) => boolean,
  change: (post: PostOut) => Partial<PostOut>,
) {
  const apply = <T>(item: T): T =>
    isPost(item) && match(item) ? { ...item, ...change(item) } : item
  queryClient.setQueriesData({ queryKey: feedKeys.all }, (data: unknown) => {
    if (isPost(data)) return apply(data)
    if (isInfinite(data)) {
      return {
        ...data,
        pages: data.pages.map((page) => ({ ...page, items: page.items.map(apply) })),
      }
    }
    return data
  })
}

/** Mark lists stale without refetching the ones on screen now. */
function markListsStale(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: feedKeys.all, refetchType: 'none' })
}

const byDeed = (deedId: string) => (post: PostOut) => post.deed_id === deedId

// ----------------------------------------------------------------- like

export function useToggleLike() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ deedId, liked }: { deedId: string; liked: boolean }) =>
      liked ? feedApi.like(deedId) : feedApi.unlike(deedId),
    onMutate: ({ deedId, liked }) =>
      patchPosts(queryClient, byDeed(deedId), (post) =>
        post.liked === liked
          ? {}
          : { liked, like_count: Math.max(0, post.like_count + (liked ? 1 : -1)) },
      ),
    onSuccess: (result, { deedId }) =>
      patchPosts(queryClient, byDeed(deedId), () => ({
        liked: result.liked,
        like_count: result.like_count,
      })),
    onError: (_error, { deedId, liked }) =>
      patchPosts(queryClient, byDeed(deedId), (post) =>
        post.liked === liked
          ? { liked: !liked, like_count: Math.max(0, post.like_count + (liked ? -1 : 1)) }
          : {},
      ),
    onSettled: () => markListsStale(queryClient),
  })
}

// ----------------------------------------------------------------- save

export function useToggleSave() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ deedId, saved }: { deedId: string; saved: boolean }) =>
      saved ? feedApi.save(deedId) : feedApi.unsave(deedId),
    onMutate: ({ deedId, saved }) => patchPosts(queryClient, byDeed(deedId), () => ({ saved })),
    onSuccess: (result, { deedId }) =>
      patchPosts(queryClient, byDeed(deedId), () => ({ saved: result.saved })),
    onError: (_error, { deedId, saved }) =>
      patchPosts(queryClient, byDeed(deedId), () => ({ saved: !saved })),
    onSettled: () => markListsStale(queryClient),
  })
}

// ----------------------------------------------------------------- follow

function patchFollow(queryClient: QueryClient, handle: string, follow: boolean) {
  const key = handle.toLowerCase()
  patchPosts(
    queryClient,
    (post) => post.doer.handle.toLowerCase() === key,
    () => ({ following_doer: follow }),
  )
  queryClient.setQueryData<SocialOut>(feedKeys.social(handle), (social) => {
    if (!social || social.is_following === follow) return social
    return {
      ...social,
      is_following: follow,
      followers: Math.max(0, social.followers + (follow ? 1 : -1)),
    }
  })
  queryClient.setQueryData<InfinitePages<ConnectionOut>>(feedKeys.following, (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            items: page.items.map((row) =>
              row.user.handle.toLowerCase() === key ? { ...row, following: follow } : row,
            ),
          })),
        }
      : data,
  )
  queryClient.setQueryData<InfinitePages<ConnectionOut>>(feedKeys.followers, (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            items: page.items.map((row) =>
              row.user.handle.toLowerCase() === key ? { ...row, following: follow } : row,
            ),
          })),
        }
      : data,
  )
}

/** Follow or unfollow `handle`: cards, the profile counts and my connection lists. */
export function useToggleFollow(myHandle: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ handle, follow }: { handle: string; follow: boolean }) =>
      follow ? feedApi.follow(handle) : feedApi.unfollow(handle),
    onMutate: ({ handle, follow }) => patchFollow(queryClient, handle, follow),
    onSuccess: (social, { handle }) => queryClient.setQueryData(feedKeys.social(handle), social),
    onError: (_error, { handle, follow }) => patchFollow(queryClient, handle, !follow),
    onSettled: () => {
      markListsStale(queryClient)
      // My own "following" count.
      if (myHandle) void queryClient.invalidateQueries({ queryKey: feedKeys.social(myHandle) })
    },
  })
}
