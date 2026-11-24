import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { errorMessage } from '../../lib/api'
import { feedKeys, type PostDetail, type Visibility } from './feedApi'
import { useSetHidden } from './feedQueries'
import { firstName } from './feedText'
import { EyeIcon, EyeOffIcon, focusRing, primaryButton } from './feedUi'
import { useOnboardingGate } from './OnboardingPrompt'

/**
 * Hide / show for the doer and the reporter (F7 §2, M3-DESIGN §6.3). Each person's hide is their
 * own (Q24 = A): only the person who hid the deed can show it again, so when the other person
 * hid it this shows who did and offers no button. Hiding never touches verification, counts,
 * points or prizes, and the copy says so.
 */
export function VisibilityControl({ deed }: { deed: PostDetail }) {
  const visibility = deed.visibility
  const queryClient = useQueryClient()
  const setHidden = useSetHidden(deed.deed_id)
  const gate = useOnboardingGate()
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  if (!visibility || !deed.my_role) return null

  const isReporter = deed.my_role === 'reporter'
  const other = firstName((isReporter ? deed.doer : deed.reporter).display_name)
  const countsOn = isReporter ? `${firstName(deed.doer.display_name)}’s profile` : 'your profile'

  const run = (hidden: boolean) =>
    setHidden.mutate(hidden, {
      onSuccess: (next: Visibility) => {
        queryClient.setQueryData<PostDetail>(feedKeys.deed(deed.deed_id), (old) =>
          old ? { ...old, visibility: next } : old,
        )
        setConfirming(false)
        setError(null)
        setMessage(
          hidden
            ? next.listed
              ? null
              : 'Hidden from the feed.'
            : next.listed
              ? 'It’s back in the public feed.'
              : null,
        )
      },
      onError: (err) => {
        if (gate.handleError(err)) {
          setConfirming(false)
          return
        }
        setError(errorMessage(err))
      },
    })

  const hidePrompt = confirming && (
    <ConfirmDialog
      title="Hide this deed from the feed?"
      body={`It stays verified and still counts on ${countsOn} and for points. Its share link stops working. You can show it again at any time.`}
      busy={setHidden.isPending}
      error={error}
      onDismiss={() => {
        setConfirming(false)
        setError(null)
      }}
      actions={[
        { label: 'Hide from feed', variant: 'primary', onClick: () => run(true) },
        {
          label: 'Cancel',
          variant: 'secondary',
          autoFocus: true,
          onClick: () => {
            setConfirming(false)
            setError(null)
          },
        },
      ]}
    />
  )

  const status = message && (
    <p role="status" className="text-sm font-medium text-emerald-800">
      {message}
    </p>
  )

  if (!visibility.hidden_by_me && !visibility.hidden_by_other) {
    return (
      <>
        <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white py-2.5 pr-3 pl-3.5 text-sm text-slate-700">
          <EyeIcon className="size-5 shrink-0 text-emerald-700" />
          <span className="grow">
            {isReporter
              ? 'You reported this deed. It’s in the public feed.'
              : 'This deed is about you. It’s in the public feed.'}
          </span>
          {visibility.can_hide && (
            <button
              type="button"
              onClick={() => gate.guard(() => setConfirming(true))}
              className={`min-h-11 shrink-0 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`}
            >
              Hide from feed
            </button>
          )}
        </div>
        {status}
        {hidePrompt}
        {gate.prompt}
      </>
    )
  }

  if (visibility.hidden_by_me) {
    return (
      <>
        <div className="flex flex-col gap-2.5 rounded-xl border border-slate-300 bg-slate-100 p-3.5 text-[15px] text-slate-700">
          <div className="flex items-start gap-2.5">
            <EyeOffIcon className="mt-0.5 size-5 shrink-0" />
            <p>
              <strong className="block text-slate-900">Hidden from the feed</strong>
              Only you and {other} can see it. It’s still verified and still counts on {countsOn}.
              Its share link is turned off.
              {visibility.hidden_by_other &&
                ` ${other} has hidden it too, so it stays hidden until they show it again.`}
            </p>
          </div>
          {visibility.can_show && (
            <button
              type="button"
              onClick={() => gate.guard(() => run(false))}
              disabled={setHidden.isPending}
              className={`${primaryButton} min-h-11 self-start px-4 text-[15px]`}
            >
              {setHidden.isPending ? 'Showing…' : 'Show in feed'}
            </button>
          )}
          {error && (
            <p role="alert" className="text-sm font-medium text-red-700">
              {error}
            </p>
          )}
        </div>
        {status}
        {gate.prompt}
      </>
    )
  }

  // Hidden by the other person only (Q24 = A): they alone can show it again.
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-slate-300 bg-slate-100 p-3.5 text-[15px] text-slate-700">
      <EyeOffIcon className="mt-0.5 size-5 shrink-0" />
      <p>
        <strong className="block text-slate-900">{other} hid this deed from the feed</strong>
        Only you and {other} can see it. It’s still verified and still counts on {countsOn}. Its
        share link is turned off.
      </p>
    </div>
  )
}
