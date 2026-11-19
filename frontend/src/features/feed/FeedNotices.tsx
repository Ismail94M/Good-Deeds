import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import type { CommentNotice } from './feedApi'
import {
  useCommentNotices,
  useMarkCommentNoticeSeen,
  useMarkNoticeSeen,
  useNotices,
} from './feedQueries'
import { focusRing, primaryButton, secondaryButton } from './feedUi'

const PUBLIC_FEED_INTRO = 'public_feed_intro'

/**
 * One-time cards above the feed: the public-feed notice (Q41, once per person, stored on the
 * server) and one card per comment of mine removed by moderation (§3.4a), oldest first. Each
 * card disappears as soon as it is dismissed; the server records it.
 */
export function FeedNotices() {
  return (
    <>
      <CommentRemovedNotices />
      <PublicFeedNotice />
    </>
  )
}

function PublicFeedNotice() {
  const notices = useNotices()
  const markSeen = useMarkNoticeSeen()
  const navigate = useNavigate()
  const [dismissed, setDismissed] = useState(false)
  if (dismissed || !notices.data?.includes(PUBLIC_FEED_INTRO)) return null

  const dismiss = () => {
    setDismissed(true)
    markSeen.mutate(PUBLIC_FEED_INTRO)
  }

  return (
    <section
      aria-label="About the public feed"
      className="flex flex-col gap-3 rounded-2xl border border-brand-100 bg-brand-50 p-4"
    >
      <p className="text-[15px] text-slate-800">
        Deeds people record about you now appear in the public feed with your name and the area. You
        can hide any of them from the deed’s page.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={`${primaryButton} min-h-11 px-4 text-[15px]`}
          onClick={() => {
            dismiss()
            navigate('/me/deeds')
          }}
        >
          See my deeds
        </button>
        <button type="button" className={secondaryButton} onClick={dismiss}>
          OK
        </button>
      </div>
    </section>
  )
}

function CommentRemovedNotices() {
  const notices = useCommentNotices()
  const markSeen = useMarkCommentNoticeSeen()
  const [dismissed, setDismissed] = useState<string[]>([])
  const visible = (notices.data ?? [])
    .filter((notice) => !dismissed.includes(notice.comment_id))
    .sort((a, b) => Date.parse(a.removed_at) - Date.parse(b.removed_at))
  if (visible.length === 0) return null

  return (
    <>
      {visible.map((notice) => (
        <CommentRemovedCard
          key={notice.comment_id}
          notice={notice}
          onOk={() => {
            setDismissed((ids) => [...ids, notice.comment_id])
            markSeen.mutate(notice.comment_id)
          }}
        />
      ))}
    </>
  )
}

function CommentRemovedCard({ notice, onOk }: { notice: CommentNotice; onOk: () => void }) {
  const titleId = `comment-notice-${notice.comment_id}`
  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-4"
    >
      <h2 id={titleId} className="font-bold text-slate-900">
        Your comment on {notice.doer.display_name}’s deed was removed
      </h2>
      <p className="text-[15px] text-slate-700">
        It broke our community guidelines.
        {notice.excerpt && <> “{notice.excerpt}…”</>}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Link
          to="/community-guidelines"
          className={`inline-flex min-h-11 items-center font-semibold text-brand-700 underline underline-offset-2 hover:text-brand-900 ${focusRing}`}
        >
          Read the community guidelines
        </Link>
        <button type="button" className={secondaryButton} onClick={onOk}>
          OK
        </button>
      </div>
    </section>
  )
}
