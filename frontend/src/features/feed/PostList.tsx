import type { InfiniteData, UseInfiniteQueryResult } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { ErrorAlert } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import type { Page, PostOut } from './feedApi'
import { FeedSkeleton } from './FeedSkeleton'
import { secondaryButton } from './feedUi'
import { PostCard } from './PostCard'
import { useSignedUrlRefresh } from './useSignedUrlRefresh'

/** Call `onVisible` when the element scrolls into view (no-op where IntersectionObserver is missing). */
export function useOnVisible(onVisible: () => void, enabled: boolean, resetKey: number) {
  const ref = useRef<HTMLDivElement>(null)
  const callback = useRef(onVisible)
  useEffect(() => {
    callback.current = onVisible
  }, [onVisible])
  useEffect(() => {
    const el = ref.current
    if (!enabled || !el || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) callback.current()
      },
      { rootMargin: '600px 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
    // Observe again after each page, so a sentinel that stays in view loads the next one too.
  }, [enabled, resetKey])
  return ref
}

/**
 * A paged list of post cards: skeleton, error with retry, empty state, infinite scroll with a
 * "Show more" fallback, and an end message. Keeps the signed photo URLs fresh (§4.7).
 */
export function PostList({
  query,
  empty,
  end,
  rankLabel,
  loadingLabel,
}: {
  query: UseInfiniteQueryResult<InfiniteData<Page<PostOut>>>
  empty: ReactNode
  end?: ReactNode
  rankLabel?: (post: PostOut) => string | null
  loadingLabel?: string
}) {
  const {
    data,
    error,
    isPending,
    isError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    refetch,
  } = query
  const posts = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data])
  const photos = useMemo(() => posts.flatMap((post) => post.photos), [posts])
  const onImageError = useSignedUrlRefresh(photos, refetch)
  const loadMore = () => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }
  const sentinel = useOnVisible(loadMore, Boolean(hasNextPage) && !isFetchingNextPage, posts.length)

  if (isPending) return <FeedSkeleton label={loadingLabel} />
  if (isError && posts.length === 0) {
    return (
      <div className="flex flex-col items-start gap-3">
        <ErrorAlert>{errorMessage(error)}</ErrorAlert>
        <button type="button" className={secondaryButton} onClick={() => void refetch()}>
          Try again
        </button>
      </div>
    )
  }
  if (posts.length === 0) return <>{empty}</>

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-4">
        {posts.map((post) => (
          <li key={post.deed_id}>
            <PostCard
              post={post}
              rankLabel={rankLabel?.(post) ?? null}
              onImageError={onImageError}
            />
          </li>
        ))}
      </ul>
      {hasNextPage ? (
        <div ref={sentinel} className="flex justify-center">
          <button
            type="button"
            className={secondaryButton}
            onClick={loadMore}
            disabled={isFetchingNextPage}
          >
            {isFetchingNextPage ? 'Loading…' : 'Show more'}
          </button>
        </div>
      ) : (
        end
      )}
      {isError && posts.length > 0 && <ErrorAlert>{errorMessage(error)}</ErrorAlert>}
    </div>
  )
}
