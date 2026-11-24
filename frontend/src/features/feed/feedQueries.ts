/**
 * TanStack Query hooks for the feed and social API (M3-DESIGN §3.4, §6.4). Reads and plain
 * mutations with invalidation; screens that need optimistic updates (like, save, follow; F10)
 * build them on top of these keys.
 */

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import {
  DEFAULT_TOP_PERIOD,
  feedApi,
  feedKeys,
  type CommentReportRequest,
  type FeedTab,
  type NoticeKey,
  type ShareTarget,
  type TopPeriod,
} from './feedApi'

const firstPage = null as string | null
const nextCursor = (page: { next_cursor: string | null }) => page.next_cursor

/** Retry network and server errors (up to 3 times), never a 4xx such as a 404 for a hidden deed. */
export function retryUnlessClientError(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return failureCount < 3
}

// ----------------------------------------------------------------- reads

/** `GET /feed/options`: comment length, Top badge ranks and periods, report reasons. */
export function useFeedOptions() {
  return useQuery({
    queryKey: feedKeys.options,
    queryFn: feedApi.options,
    staleTime: 5 * 60_000,
  })
}

/** The home feed for one tab (and Top period), page by page. */
export function useFeed(tab: FeedTab, period: TopPeriod = DEFAULT_TOP_PERIOD) {
  return useInfiniteQuery({
    queryKey: feedKeys.list(tab, period),
    queryFn: ({ pageParam }) => feedApi.feed({ tab, period, cursor: pageParam }),
    initialPageParam: firstPage,
    getNextPageParam: nextCursor,
  })
}

/** The stories bar (D73): who in my network has stories. Counts only, so it is cheap. */
export function useStoryRings() {
  return useQuery({
    queryKey: feedKeys.stories,
    queryFn: feedApi.storyRings,
    staleTime: 60_000,
    retry: retryUnlessClientError,
  })
}

/** One person's stories with signed photo URLs; loaded only when their ring is opened. */
export function useStoryPerson(handle: string | null) {
  return useQuery({
    queryKey: feedKeys.storyPerson(handle ?? ''),
    queryFn: () => feedApi.storyPerson(handle ?? ''),
    enabled: handle !== null,
    staleTime: 5 * 60_000,
    retry: retryUnlessClientError,
  })
}

/** `PostDetail` for `/deeds/:id`. */
export function useFeedDeed(deedId: string) {
  return useQuery({
    queryKey: feedKeys.deed(deedId),
    queryFn: () => feedApi.deed(deedId),
    retry: retryUnlessClientError,
  })
}

/** Live comments on a deed, oldest first. */
export function useComments(deedId: string) {
  return useInfiniteQuery({
    queryKey: feedKeys.comments(deedId),
    queryFn: ({ pageParam }) => feedApi.comments(deedId, pageParam),
    initialPageParam: firstPage,
    getNextPageParam: nextCursor,
  })
}

/** The public share page (no auth). */
export function usePublicDeed(deedId: string) {
  return useQuery({
    queryKey: feedKeys.publicDeed(deedId),
    queryFn: () => feedApi.publicDeed(deedId),
    retry: retryUnlessClientError,
  })
}

/**
 * Follower and following counts, and whether I follow them. Login is optional on this read, so it
 * waits until the session is restored: asked earlier (a profile opened straight from a link), the
 * server answers as a visitor and the cached reply shows "Follow" to someone already following,
 * or on their own profile.
 */
export function useSocial(handle: string) {
  const { isLoading } = useAuth()
  return useQuery({
    queryKey: feedKeys.social(handle),
    queryFn: () => feedApi.social(handle),
    retry: retryUnlessClientError,
    enabled: !isLoading,
  })
}

/** A person's listed deeds as the doer, newest first. */
export function useUserDeeds(handle: string) {
  return useInfiniteQuery({
    queryKey: feedKeys.userDeeds(handle),
    queryFn: ({ pageParam }) => feedApi.userDeeds(handle, pageParam),
    initialPageParam: firstPage,
    getNextPageParam: nextCursor,
  })
}

/** My saved deeds, newest save first. */
export function useSaved() {
  return useInfiniteQuery({
    queryKey: feedKeys.saved,
    queryFn: ({ pageParam }) => feedApi.saved(pageParam),
    initialPageParam: firstPage,
    getNextPageParam: nextCursor,
  })
}

/** My own followers or following list (Q36). */
export function useConnections(list: 'followers' | 'following') {
  return useInfiniteQuery({
    queryKey: list === 'followers' ? feedKeys.followers : feedKeys.following,
    queryFn: ({ pageParam }) =>
      list === 'followers' ? feedApi.myFollowers(pageParam) : feedApi.myFollowing(pageParam),
    initialPageParam: firstPage,
    getNextPageParam: nextCursor,
  })
}

/** Unseen one-time notice keys (Q41). */
export function useNotices(enabled = true) {
  return useQuery({ queryKey: feedKeys.notices, queryFn: feedApi.notices, enabled })
}

/** My comments removed by moderation and not yet dismissed (§3.4a). */
export function useCommentNotices(enabled = true) {
  return useQuery({ queryKey: feedKeys.commentNotices, queryFn: feedApi.commentNotices, enabled })
}

// ----------------------------------------------------------------- invalidation

/** Refresh a deed and every list it can appear in (§6.4). */
export function useInvalidatePost() {
  const queryClient = useQueryClient()
  return (deedId: string) => {
    void queryClient.invalidateQueries({ queryKey: feedKeys.deed(deedId) })
    for (const tab of ['nearby', 'following', 'top'] as const) {
      void queryClient.invalidateQueries({ queryKey: ['feed', tab] })
    }
    void queryClient.invalidateQueries({ queryKey: feedKeys.saved })
    void queryClient.invalidateQueries({ queryKey: ['feed', 'user-deeds'] })
  }
}

// ----------------------------------------------------------------- writes

export function useLike() {
  const invalidate = useInvalidatePost()
  return useMutation({
    mutationFn: ({ deedId, liked }: { deedId: string; liked: boolean }) =>
      liked ? feedApi.like(deedId) : feedApi.unlike(deedId),
    onSettled: (_data, _error, { deedId }) => invalidate(deedId),
  })
}

export function useSave() {
  const invalidate = useInvalidatePost()
  return useMutation({
    mutationFn: ({ deedId, saved }: { deedId: string; saved: boolean }) =>
      saved ? feedApi.save(deedId) : feedApi.unsave(deedId),
    onSettled: (_data, _error, { deedId }) => invalidate(deedId),
  })
}

export function useAddComment(deedId: string) {
  const queryClient = useQueryClient()
  const invalidate = useInvalidatePost()
  return useMutation({
    mutationFn: (body: string) => feedApi.addComment(deedId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: feedKeys.comments(deedId) })
      invalidate(deedId)
    },
  })
}

export function useDeleteComment(deedId: string) {
  const queryClient = useQueryClient()
  const invalidate = useInvalidatePost()
  return useMutation({
    mutationFn: (commentId: string) => feedApi.deleteComment(commentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: feedKeys.comments(deedId) })
      invalidate(deedId)
    },
  })
}

export function useReportComment() {
  return useMutation({
    mutationFn: ({ commentId, report }: { commentId: string; report: CommentReportRequest }) =>
      feedApi.reportComment(commentId, report),
  })
}

/** Hide (`hidden: true`) or show the deed for my own role (Q24). */
export function useSetHidden(deedId: string) {
  const invalidate = useInvalidatePost()
  return useMutation({
    mutationFn: (hidden: boolean) => (hidden ? feedApi.hide(deedId) : feedApi.show(deedId)),
    onSuccess: () => invalidate(deedId),
  })
}

export function useShare() {
  return useMutation({
    mutationFn: ({ deedId, target }: { deedId: string; target: ShareTarget }) =>
      feedApi.share(deedId, target),
  })
}

export function useFollow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ handle, follow }: { handle: string; follow: boolean }) =>
      follow ? feedApi.follow(handle) : feedApi.unfollow(handle),
    onSuccess: (social, { handle }) => {
      queryClient.setQueryData(feedKeys.social(handle), social)
      void queryClient.invalidateQueries({ queryKey: feedKeys.following })
      void queryClient.invalidateQueries({ queryKey: feedKeys.followers })
      // `following_doer` on cards, and the Following tab's contents.
      void queryClient.invalidateQueries({ queryKey: ['feed', 'nearby'] })
      void queryClient.invalidateQueries({ queryKey: ['feed', 'following'] })
      void queryClient.invalidateQueries({ queryKey: ['feed', 'top'] })
      void queryClient.invalidateQueries({ queryKey: ['feed', 'deed'] })
    },
  })
}

export function useRemoveFollower() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (handle: string) => feedApi.removeFollower(handle),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: feedKeys.followers })
      void queryClient.invalidateQueries({ queryKey: ['social'] })
    },
  })
}

export function useMarkNoticeSeen() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (key: NoticeKey) => feedApi.markNoticeSeen(key),
    onSuccess: (_data, key) => {
      queryClient.setQueryData<NoticeKey[]>(feedKeys.notices, (keys) =>
        keys?.filter((k) => k !== key),
      )
    },
  })
}

export function useMarkCommentNoticeSeen() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (commentId: string) => feedApi.markCommentNoticeSeen(commentId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: feedKeys.commentNotices }),
  })
}
