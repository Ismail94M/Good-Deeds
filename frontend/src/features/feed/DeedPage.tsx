import { useId, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { BackLink } from '../deeds/deedUi'
import type { PhotoOut } from '../media/mediaApi'
import { CommentsSection } from './CommentsSection'
import { useFeedDeed } from './feedQueries'
import { helpedLine } from './feedText'
import { EyeOffIcon, FeedMain, primaryButton, secondaryButton } from './feedUi'
import { PostCard } from './PostCard'
import { useRemovePhoto } from './useRemovePhoto'
import { useSignedUrlRefresh } from './useSignedUrlRefresh'
import { VisibilityControl } from './VisibilityControl'

/** "This deed isn't available": shared by the deed page and the public page. */
export function DeedUnavailable({ action }: { action: { to: string; label: string } }) {
  return (
    <div className="flex flex-col items-center gap-3 px-2 py-12 text-center">
      <span
        aria-hidden
        className="flex size-15 items-center justify-center rounded-full bg-slate-100 text-slate-600"
      >
        <EyeOffIcon className="size-7.5" />
      </span>
      <h1 className="text-2xl font-bold text-slate-900">This deed isn’t available</h1>
      <p className="max-w-[320px] text-slate-600">
        It may have been hidden or removed by the people involved.
      </p>
      <Link to={action.to} className={primaryButton}>
        {action.label}
      </Link>
    </div>
  )
}

/**
 * `/deeds/:id` (F7 §2, M3-DESIGN §6.3): the post card, "Helped {n} people · Checked by QR code
 * and location", hide / show for the doer and reporter, and the comments.
 */
export function DeedPage() {
  const { id = '' } = useParams()
  const deed = useFeedDeed(id)
  const composerId = useId()
  const onImageError = useSignedUrlRefresh(deed.data?.photos ?? [], deed.refetch)
  const removePhoto = useRemovePhoto(id)
  const [toRemove, setToRemove] = useState<PhotoOut | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)
  const [removed, setRemoved] = useState(false)

  if (deed.isPending) return <Loading label="Loading the deed…" />

  if (deed.isError) {
    if (deed.error instanceof ApiError && deed.error.status === 404) {
      return (
        <FeedMain>
          <DeedUnavailable action={{ to: '/', label: 'Back to the feed' }} />
        </FeedMain>
      )
    }
    return (
      <FeedMain>
        <BackLink to="/" label="Feed" />
        <ErrorAlert>{errorMessage(deed.error)}</ErrorAlert>
        <button
          type="button"
          className={`${secondaryButton} self-start`}
          onClick={() => void deed.refetch()}
        >
          Try again
        </button>
      </FeedMain>
    )
  }

  const post = deed.data
  const helped = helpedLine(post.beneficiaries_count)
  return (
    <FeedMain>
      <BackLink to="/" label="Feed" />
      <VisibilityControl deed={post} />
      <PostCard
        post={post}
        headingLevel={1}
        shareable={post.visibility ? post.visibility.listed : true}
        onComments={() => document.getElementById(composerId)?.focus()}
        onImageError={onImageError}
        onRemovePhoto={
          post.my_role === 'doer'
            ? (photo) => {
                setRemoveError(null)
                setToRemove(photo)
              }
            : undefined
        }
      />
      {removed && (
        <p role="status" className="text-sm font-medium text-emerald-800">
          Photo removed.
        </p>
      )}
      <p className="-mt-1 text-sm text-slate-600">
        {helped && `${helped} · `}
        {post.verification === 'invite_accept'
          ? 'Checked by invite link'
          : 'Checked by QR code and location'}
      </p>
      <CommentsSection deed={post} composerId={composerId} />
      {toRemove && (
        // Q29: final; the reporter can't restore it and nothing about the deed changes.
        <ConfirmDialog
          title="Remove this photo?"
          body={`It will be removed from the feed, this deed and its share link. ${post.reporter.display_name} can’t put it back. Your deed stays verified and your points don’t change.`}
          busy={removePhoto.isPending}
          error={removeError}
          onDismiss={() => setToRemove(null)}
          actions={[
            {
              label: 'Remove photo',
              variant: 'danger',
              onClick: () =>
                removePhoto.mutate(toRemove.id, {
                  onSuccess: () => {
                    setToRemove(null)
                    setRemoved(true)
                  },
                  onError: (err) => setRemoveError(errorMessage(err)),
                }),
            },
            {
              label: 'Keep it',
              variant: 'secondary',
              autoFocus: true,
              onClick: () => setToRemove(null),
            },
          ]}
        />
      )}
    </FeedMain>
  )
}
