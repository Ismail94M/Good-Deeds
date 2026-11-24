import { BackLink, LockIcon } from '../deeds/deedUi'
import { useSaved } from './feedQueries'
import { BookmarkIcon, EmptyBox, FeedMain } from './feedUi'
import { PostList } from './PostList'

/**
 * `/me/saved` (F7 "Saved"): my saved deeds, newest save first. Saves are private and never
 * counted. A deed that is no longer viewable silently drops out (§3.4 #12).
 */
export function SavedPage() {
  const saved = useSaved()
  return (
    <FeedMain>
      <BackLink to="/profile" label="Profile" />
      <h1 className="text-[26px] leading-tight font-bold tracking-tight text-slate-900 sm:text-[32px]">
        Saved
      </h1>
      <p className="flex items-center gap-2 text-slate-600">
        <LockIcon className="size-4.5 shrink-0" />
        Only you can see what you save.
      </p>
      <PostList
        query={saved}
        loadingLabel="Loading your saved deeds"
        empty={
          <EmptyBox
            icon={<BookmarkIcon filled={false} />}
            title="Nothing saved yet"
            body="Tap the bookmark on a deed to keep it here."
          />
        }
      />
    </FeedMain>
  )
}
