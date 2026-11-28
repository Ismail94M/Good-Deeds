import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth/authContext'
import { categoryLabel } from '../deeds/deedText'
import type { PhotoOut } from '../media/mediaApi'
import type { PostOut, PublicDeed } from './feedApi'
import { useShare } from './feedQueries'
import { recognisedBy, timeAgo } from './feedText'
import {
  AvatarLink,
  Avatar,
  BookmarkIcon,
  CommentIcon,
  DotsIcon,
  FollowButton,
  focusRing,
  HeartIcon,
  RankChip,
  ShareIcon,
  VerifiedChip,
} from './feedUi'
import { useOnboardingGate } from './OnboardingPrompt'
import { PhotoCarousel } from './PhotoCarousel'
import { PostMenuSheet } from './PostMenuSheet'
import { useToggleFollow, useToggleLike, useToggleSave } from './postActions'
import { ReportPhotoSheet } from './reportPhotoSlot'
import { copyText } from './shareHelpers'
import { ShareSheet } from './ShareSheet'
import { useToast } from './useToast'

const actionClass = `inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2.5 text-[15px] font-semibold text-slate-700 hover:bg-slate-50 ${focusRing}`

type Sheet = 'share' | 'menu' | 'report' | null

/** The API's label (§3.4b 11); a readable form of the slug only if it is missing. */
function cardCategory(deed: { category: string; category_label?: string | null }): string {
  return deed.category_label || categoryLabel(undefined, deed.category)
}

/** The card's top part, shared by the member card and the public page card. */
function CardHead({
  doer,
  asHeading = false,
  byLine,
  category,
  area,
  occurredAt,
  avatar,
  trailing,
  chips,
}: {
  doer: string
  /** The deed page's main heading is the doer's name. */
  asHeading?: boolean
  byLine: string
  category: string
  area: string
  occurredAt: string
  avatar: ReactNode
  trailing?: ReactNode
  chips?: ReactNode
}) {
  return (
    <>
      <div className="flex items-center gap-3 px-4 pt-3.5 pb-1.5">
        {avatar}
        <div className="min-w-0 grow">
          {asHeading ? (
            <h1 className="leading-tight font-bold break-words text-slate-900">{doer}</h1>
          ) : (
            <p className="leading-tight font-bold break-words text-slate-900">{doer}</p>
          )}
          <p className="text-[13px] text-slate-600">{byLine}</p>
        </div>
        {trailing}
      </div>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 pb-2.5 pl-18 text-[13px] text-slate-600">
        <span className="font-semibold text-brand-900">{category}</span>
        <span aria-hidden>·</span>
        <span>{area}</span>
        <span aria-hidden>·</span>
        <time dateTime={occurredAt}>{timeAgo(occurredAt)}</time>
      </p>
      <div className="flex flex-wrap gap-2 px-4 pb-3">
        <VerifiedChip />
        {chips}
      </div>
    </>
  )
}

function MenuButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label="More options for this deed"
      onClick={onClick}
      className={`flex size-11 shrink-0 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-50 ${focusRing}`}
    >
      <DotsIcon />
    </button>
  )
}

/**
 * A post card (F7 §1): doer first, "recognised by …", Follow, category · area · time, Verified
 * chip, Top rank chip, photos, description and the actions. Like, save and follow are optimistic
 * and need a finished setup (D44). On the deed page the comment button jumps to the composer.
 */
export function PostCard({
  post,
  rankLabel = null,
  shareable = true,
  onComments,
  onImageError,
  onRemovePhoto,
  headingLevel,
}: {
  post: PostOut
  /** "#1 this week": only ranks within `top_badge_ranks`. */
  rankLabel?: string | null
  /** False for a hidden deed: no share link exists (409 `not_shareable`). */
  shareable?: boolean
  /** On the deed page: focus the composer instead of opening the deed. */
  onComments?: () => void
  onImageError?: () => void
  /** Deed page, doer only (Q29): offer a ✕ on the photo showing. */
  onRemovePhoto?: (photo: PhotoOut) => void
  /** Render the doer's name as a heading (the deed page's main heading). */
  headingLevel?: 1
}) {
  const { user } = useAuth()
  const gate = useOnboardingGate()
  const like = useToggleLike()
  const save = useToggleSave()
  const follow = useToggleFollow(user?.handle)
  const share = useShare()
  const [photoIndex, setPhotoIndex] = useState(0)
  const [sheet, setSheet] = useState<Sheet>(null)
  const [toast, showToast] = useToast()

  const failed = (error: unknown) => {
    if (!gate.handleError(error)) showToast('That didn’t work. Please try again.')
  }

  const isDoer = post.my_role === 'doer'
  const photo = post.photos[Math.min(photoIndex, post.photos.length - 1)]
  // Only the reporter uploads photos on a deed (§3.3 #5), so "not the uploader" = not the reporter.
  const canReportPhoto = Boolean(ReportPhotoSheet && photo && post.my_role !== 'reporter')
  const deedHref = `/deeds/${encodeURIComponent(post.deed_id)}`

  const record = (target: Parameters<typeof share.mutate>[0]['target']) => {
    // Recording needs a finished setup; the link itself works either way.
    if (gate.ready) share.mutate({ deedId: post.deed_id, target })
  }

  const copyLink = async () => {
    setSheet(null)
    const ok = await copyText(post.share_url)
    if (ok) record('copy_link')
    showToast(ok ? 'Link copied' : 'Couldn’t copy the link. Please try again.')
  }

  return (
    <article
      aria-label={`Deed by ${post.doer.display_name}`}
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs"
    >
      <CardHead
        doer={post.doer.display_name}
        asHeading={headingLevel === 1}
        byLine={recognisedBy(post)}
        category={cardCategory(post)}
        area={post.area_name}
        occurredAt={post.occurred_at}
        avatar={
          <AvatarLink
            name={post.doer.display_name}
            handle={post.doer.handle}
            avatar={post.doer.avatar}
          />
        }
        trailing={
          <>
            {!isDoer && (
              <FollowButton
                following={post.following_doer}
                name={post.doer.display_name}
                onClick={() =>
                  gate.guard(() =>
                    follow.mutate(
                      { handle: post.doer.handle, follow: !post.following_doer },
                      { onError: failed },
                    ),
                  )
                }
              />
            )}
            {(canReportPhoto || shareable) && <MenuButton onClick={() => setSheet('menu')} />}
          </>
        }
        chips={rankLabel && <RankChip>{rankLabel}</RankChip>}
      />
      <PhotoCarousel
        photos={post.photos}
        index={photoIndex}
        onIndexChange={setPhotoIndex}
        onImageError={onImageError}
        onRemove={onRemovePhoto}
      />
      <p className="px-4 pt-3 pb-1 text-[15px] break-words whitespace-pre-line text-slate-800">
        {post.description}
      </p>
      <div role="group" aria-label="Actions" className="flex items-center gap-0.5 px-2 pt-0.5 pb-2">
        <button
          type="button"
          aria-pressed={post.liked}
          aria-label={`Like. ${post.like_count} ${post.like_count === 1 ? 'like' : 'likes'}`}
          onClick={() =>
            gate.guard(() =>
              like.mutate({ deedId: post.deed_id, liked: !post.liked }, { onError: failed }),
            )
          }
          className={actionClass}
        >
          <HeartIcon filled={post.liked} />
          {post.like_count}
        </button>
        {onComments ? (
          <button
            type="button"
            aria-label={`Comments. ${post.comment_count} comments`}
            onClick={onComments}
            className={actionClass}
          >
            <CommentIcon />
            {post.comment_count}
          </button>
        ) : (
          <Link
            to={deedHref}
            aria-label={`Comments. ${post.comment_count} comments`}
            className={actionClass}
          >
            <CommentIcon />
            {post.comment_count}
          </Link>
        )}
        {shareable && (
          <button type="button" onClick={() => setSheet('share')} className={actionClass}>
            <ShareIcon />
            Share
          </button>
        )}
        <span className="grow" />
        <button
          type="button"
          aria-pressed={post.saved}
          aria-label={post.saved ? 'Saved. Tap to unsave' : 'Save'}
          onClick={() =>
            gate.guard(() =>
              save.mutate({ deedId: post.deed_id, saved: !post.saved }, { onError: failed }),
            )
          }
          className={`flex size-11 items-center justify-center rounded-lg hover:bg-slate-50 ${focusRing} ${
            post.saved ? 'text-brand-700' : 'text-slate-700'
          }`}
        >
          <BookmarkIcon filled={post.saved} />
        </button>
      </div>

      {sheet === 'share' && (
        <ShareSheet
          url={post.share_url}
          subtitle={`${post.doer.display_name} · ${cardCategory(post)}`}
          onShared={record}
          onCopied={(ok) =>
            showToast(ok ? 'Link copied' : 'Couldn’t copy the link. Please try again.')
          }
          onClose={() => setSheet(null)}
        />
      )}
      {sheet === 'menu' && (
        <PostMenuSheet
          doerName={post.doer.display_name}
          onReportPhoto={canReportPhoto ? () => setSheet('report') : null}
          onCopyLink={shareable ? () => void copyLink() : null}
          onClose={() => setSheet(null)}
        />
      )}
      {sheet === 'report' && ReportPhotoSheet && photo && (
        <ReportPhotoSheet photoId={photo.id} deedId={post.deed_id} onClose={() => setSheet(null)} />
      )}
      {gate.prompt}
      {toast}
    </article>
  )
}

/**
 * The card on the public share page (F7 §3): no action buttons, "{n} likes · {n} comments", a
 * "⋯" menu with Report photo and Copy link. Names, likes and comments lead to sign-up, which
 * brings the visitor back to the member page.
 */
export function PublicPostCard({ deed, joinHref }: { deed: PublicDeed; joinHref: string }) {
  const [photoIndex, setPhotoIndex] = useState(0)
  const [sheet, setSheet] = useState<Sheet>(null)
  const [toast, showToast] = useToast()
  const photo = deed.photos[Math.min(photoIndex, deed.photos.length - 1)]
  const canReportPhoto = Boolean(ReportPhotoSheet && photo)
  const shareUrl = `${window.location.origin}/d/${encodeURIComponent(deed.deed_id)}`

  const copyLink = async () => {
    setSheet(null)
    const ok = await copyText(shareUrl)
    showToast(ok ? 'Link copied' : 'Couldn’t copy the link. Please try again.')
  }

  return (
    <article
      aria-label={`Deed by ${deed.doer.display_name}`}
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white"
    >
      <CardHead
        doer={deed.doer.display_name}
        byLine={`recognised by ${deed.reporter.display_name}`}
        category={cardCategory(deed)}
        area={deed.area_name}
        occurredAt={deed.occurred_at}
        avatar={
          <Link
            to={joinHref}
            aria-label={`${deed.doer.display_name}, join to see their profile`}
            className={`shrink-0 rounded-full ${focusRing}`}
          >
            <Avatar name={deed.doer.display_name} handle={deed.doer.handle} avatar={null} />
          </Link>
        }
        trailing={<MenuButton onClick={() => setSheet('menu')} />}
      />
      <PhotoCarousel photos={deed.photos} index={photoIndex} onIndexChange={setPhotoIndex} />
      <p className="px-4 pt-3 pb-1 text-[15px] break-words whitespace-pre-line text-slate-800">
        {deed.description}
      </p>
      <p className="px-4 pt-1 pb-3.5 text-sm text-slate-600">
        <Link to={joinHref} className={`hover:underline ${focusRing}`}>
          {deed.like_count} {deed.like_count === 1 ? 'like' : 'likes'} · {deed.comment_count}{' '}
          {deed.comment_count === 1 ? 'comment' : 'comments'}
        </Link>
      </p>
      {sheet === 'menu' && (
        <PostMenuSheet
          doerName={deed.doer.display_name}
          onReportPhoto={canReportPhoto ? () => setSheet('report') : null}
          onCopyLink={() => void copyLink()}
          onClose={() => setSheet(null)}
        />
      )}
      {sheet === 'report' && ReportPhotoSheet && photo && (
        <ReportPhotoSheet photoId={photo.id} deedId={deed.deed_id} onClose={() => setSheet(null)} />
      )}
      {toast}
    </article>
  )
}
