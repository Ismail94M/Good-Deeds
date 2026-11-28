import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId } from 'react'
import { ErrorAlert, Loading, TextLink } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { Avatar } from '../media/avatar/Avatar'
import { FeedMain, focusRing, LockIcon, secondaryButton } from '../feed/feedUi'
import { useToast } from '../feed/useToast'
import {
  notificationsApi,
  notificationsKeys,
  useMute,
  usePreferences,
  type CategoryPref,
  type NotificationCategory,
} from './notificationsApi'

/** §6.3: why a locked category can't be switched off. */
const LOCKED_NOTE: Partial<Record<NotificationCategory, string>> = {
  account: 'Always on, so you never miss something about your account.',
  reviews: 'Always on, so you never miss something about a review.',
}

function prefError(error: unknown): string {
  if (error instanceof ApiError && error.code === 'category_locked') return 'This one is always on.'
  return errorMessage(error)
}

/**
 * `/settings/notifications` (§6.3, D113): one switch per category with a one-line description;
 * "Account & safety" and "Reviews" are always on. Below: muted people and deeds, with Unmute.
 */
export function NotificationSettingsPage() {
  const prefs = usePreferences()
  const queryClient = useQueryClient()
  const [toast, showToast] = useToast()
  const mute = useMute()
  const toggle = useMutation({
    mutationFn: ({ category, on }: { category: NotificationCategory; on: boolean }) =>
      notificationsApi.setPreference(category, on),
    onSuccess: (data) => queryClient.setQueryData(notificationsKeys.prefs, data),
    onError: (error) => showToast(prefError(error)),
  })
  const peopleId = useId()
  const deedsId = useId()

  if (prefs.isPending) return <Loading label="Loading your settings…" />
  if (prefs.isError) {
    return (
      <FeedMain label="Notification settings">
        <ErrorAlert>{errorMessage(prefs.error)}</ErrorAlert>
        <button type="button" onClick={() => void prefs.refetch()} className={secondaryButton}>
          Try again
        </button>
      </FeedMain>
    )
  }
  const { categories, mutes } = prefs.data
  const unmute = (type: 'person' | 'deed', target: string) =>
    mute.mutate(
      { type, target, mute: false },
      {
        onSuccess: () => showToast('Unmuted.'),
        onError: (error) => showToast(errorMessage(error)),
      },
    )

  return (
    <FeedMain label="Notification settings">
      <div className="flex flex-col gap-1">
        <TextLink to="/notifications" className="self-start text-sm">
          ← Notifications
        </TextLink>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
          Notification settings
        </h1>
        <p className="text-slate-600">Choose what shows up in your notifications.</p>
      </div>
      <ul className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
        {categories.map((category, index) => (
          <li key={category.id} className={index > 0 ? 'border-t border-slate-100' : undefined}>
            <CategorySwitch
              category={category}
              pending={toggle.isPending && toggle.variables?.category === category.id}
              onChange={(on) => toggle.mutate({ category: category.id, on })}
            />
          </li>
        ))}
      </ul>

      <section aria-labelledby={peopleId} className="flex flex-col gap-2">
        <h2
          id={peopleId}
          className="text-[13px] font-semibold tracking-wider text-slate-600 uppercase"
        >
          Muted people
        </h2>
        {mutes.people.length === 0 ? (
          <p className="text-sm text-slate-600">
            You haven’t muted anyone. Muting someone stops likes, comments and network updates from
            them; they aren’t told.
          </p>
        ) : (
          <ul className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
            {mutes.people.map((person, index) => (
              <li
                key={person.handle ?? person.display_name}
                className={`flex items-center gap-3 px-3.5 py-2.5 ${index > 0 ? 'border-t border-slate-100' : ''}`}
              >
                <Avatar user={person} size="md" />
                <span className="flex min-w-0 grow flex-col">
                  <span className="truncate font-semibold text-slate-900">
                    {person.display_name}
                  </span>
                  {person.handle && (
                    <span className="truncate text-sm text-slate-600">@{person.handle}</span>
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => person.handle && unmute('person', person.handle)}
                  hidden={!person.handle}
                  disabled={mute.isPending}
                  aria-label={`Unmute ${person.display_name}`}
                  className={secondaryButton}
                >
                  Unmute
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby={deedsId} className="flex flex-col gap-2">
        <h2
          id={deedsId}
          className="text-[13px] font-semibold tracking-wider text-slate-600 uppercase"
        >
          Muted deeds
        </h2>
        {mutes.deeds.length === 0 ? (
          <p className="text-sm text-slate-600">
            No muted deeds. You can turn off notifications for a deed from its notification.
          </p>
        ) : (
          <ul className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
            {mutes.deeds.map((deed, index) => (
              <li
                key={deed.deed_id}
                className={`flex items-center gap-3 px-3.5 py-2.5 ${index > 0 ? 'border-t border-slate-100' : ''}`}
              >
                <span className="min-w-0 grow text-[15px] text-slate-900">{deed.title_text}</span>
                <button
                  type="button"
                  onClick={() => unmute('deed', deed.deed_id)}
                  disabled={mute.isPending}
                  aria-label={`Unmute ${deed.title_text}`}
                  className={secondaryButton}
                >
                  Unmute
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {toast}
    </FeedMain>
  )
}

function CategorySwitch({
  category,
  pending,
  onChange,
}: {
  category: CategoryPref
  pending: boolean
  onChange: (on: boolean) => void
}) {
  const labelId = useId()
  const descId = useId()
  const note = category.locked
    ? (LOCKED_NOTE[category.id] ?? 'Always on, so you never miss something important.')
    : null
  return (
    <div className="flex items-start gap-3 px-3.5 py-3.5">
      <span className="flex min-w-0 grow flex-col gap-0.5">
        <span id={labelId} className="flex items-center gap-1.5 font-semibold text-slate-900">
          {category.label}
          {category.locked && <LockIcon className="size-3.5 text-slate-500" />}
        </span>
        <span id={descId} className="text-sm text-slate-600">
          {category.description}
          {note && <span className="block text-slate-500">{note}</span>}
        </span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={category.on}
        aria-labelledby={labelId}
        aria-describedby={descId}
        disabled={category.locked || pending}
        onClick={() => onChange(!category.on)}
        className={`-my-2 flex min-h-11 shrink-0 items-center rounded-full disabled:cursor-not-allowed ${focusRing} ${category.locked ? 'opacity-60' : ''}`}
      >
        <span
          aria-hidden
          className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${
            category.on ? 'bg-brand-600' : 'bg-slate-300'
          }`}
        >
          <span
            className={`inline-block size-5.5 rounded-full bg-white shadow transition-transform ${
              category.on ? 'translate-x-[22px]' : 'translate-x-[3px]'
            }`}
          />
        </span>
      </button>
    </div>
  )
}
