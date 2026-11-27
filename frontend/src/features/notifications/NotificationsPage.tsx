import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { Avatar } from '../media/avatar/Avatar'
import {
  DotsIcon,
  EmptyBox,
  FeedMain,
  focusRing,
  SegmentedTabs,
  secondaryButton,
} from '../feed/feedUi'
import { useOnVisible } from '../feed/PostList'
import { useToast } from '../feed/useToast'
import { BellIcon } from './NotificationBell'
import { MuteMenu, muteName } from './MuteMenu'
import {
  notificationsApi,
  notificationsKeys,
  useMute,
  useNotifications,
  useReadAll,
  useSetRead,
  useUnreadCount,
  type NotificationItem,
  type NotificationsTab,
  type UnreadCount,
} from './notificationsApi'
import { linkHref, relativeTime, relativeTimeLong } from './notificationText'

/** N4 errors in plain words (the list is refreshed either way). */
function readError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'newer_unread_exists')
      return 'There’s already a newer unread notification like this one.'
    if (error.status === 404) return 'This notification isn’t available any more.'
  }
  return errorMessage(error)
}

const TABS: { id: NotificationsTab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'unread', label: 'Unread' },
]

/**
 * `/notifications` (§6.3, D108, D114): All / Unread tabs, newest activity first. Unread rows are
 * tinted with a dot. Tapping a row marks it read and opens its link; "⋯" offers read/unread and
 * the mutes. Opening the page clears the bell's count (`POST /notifications/seen`).
 */
export function NotificationsPage() {
  const [params, setParams] = useSearchParams()
  const tab: NotificationsTab = params.get('tab') === 'unread' ? 'unread' : 'all'
  const panelId = useId()
  const queryClient = useQueryClient()
  const query = useNotifications(tab)
  const unread = useUnreadCount()
  const readAll = useReadAll()
  const setRead = useSetRead()
  const mute = useMute()
  const [menuFor, setMenuFor] = useState<NotificationItem | null>(null)
  const [toast, showToast] = useToast()

  useEffect(() => {
    void notificationsApi
      .seen()
      .then(async () => {
        await queryClient.cancelQueries({ queryKey: notificationsKeys.unread })
        queryClient.setQueryData<UnreadCount>(notificationsKeys.unread, (data) =>
          data ? { ...data, new_since_seen: 0 } : data,
        )
        void queryClient.invalidateQueries({ queryKey: notificationsKeys.unread })
      })
      .catch(() => {})
  }, [queryClient])

  const items = query.data?.pages.flatMap((page) => page.items) ?? []
  const anyUnread = (unread.data?.unread ?? 0) > 0 || items.some((item) => !item.read)
  const sentinel = useOnVisible(
    () => {
      if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage()
    },
    Boolean(query.hasNextPage),
    query.data?.pages.length ?? 0,
  )

  const muteTarget = (type: 'person' | 'deed', target: string, done: string) => {
    setMenuFor(null)
    mute.mutate(
      { type, target, mute: true },
      {
        onSuccess: () => showToast(done),
        onError: (error) => showToast(errorMessage(error)),
      },
    )
  }

  return (
    <FeedMain label="Notifications">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
          Notifications
        </h1>
        <Link
          to="/settings/notifications"
          aria-label="Notification settings"
          className={`inline-flex size-11 items-center justify-center rounded-full text-slate-700 hover:bg-slate-100 ${focusRing}`}
        >
          <GearIcon />
        </Link>
      </div>
      <SegmentedTabs
        label="Show"
        tabs={TABS}
        value={tab}
        panelId={panelId}
        onChange={(next) => setParams(next === 'all' ? {} : { tab: next }, { replace: true })}
      />
      <div id={panelId} role="tabpanel" className="flex flex-col gap-3">
        {anyUnread && (
          <button
            type="button"
            onClick={() =>
              readAll.mutate(undefined, {
                onError: (error) => showToast(errorMessage(error)),
              })
            }
            disabled={readAll.isPending}
            className={`self-end rounded px-1 text-sm font-semibold text-brand-700 hover:text-brand-900 disabled:opacity-60 ${focusRing} min-h-11`}
          >
            Mark all as read
          </button>
        )}
        {query.isPending ? (
          <Loading label="Loading notifications…" />
        ) : query.isError ? (
          <div className="flex flex-col items-start gap-3">
            <ErrorAlert>{errorMessage(query.error)}</ErrorAlert>
            <button type="button" onClick={() => void query.refetch()} className={secondaryButton}>
              Try again
            </button>
          </div>
        ) : items.length === 0 ? (
          <EmptyBox
            icon={<BellIcon className="size-7" />}
            title={tab === 'unread' ? 'You’re all caught up' : 'Nothing yet'}
            body={
              tab === 'unread'
                ? 'New notifications will show up here.'
                : 'When someone recognises you, you’ll see it here.'
            }
          />
        ) : (
          <>
            <ul className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
              {items.map((item, index) => (
                <li key={item.id} className={index > 0 ? 'border-t border-slate-100' : undefined}>
                  <NotificationRow
                    item={item}
                    onOpen={() => {
                      if (!item.read) setRead.mutate({ id: item.id, read: true })
                    }}
                    onMenu={() => setMenuFor(item)}
                  />
                </li>
              ))}
            </ul>
            <div ref={sentinel} />
            {query.hasNextPage && (
              <button
                type="button"
                onClick={() => void query.fetchNextPage()}
                disabled={query.isFetchingNextPage}
                className={`${secondaryButton} self-center`}
              >
                {query.isFetchingNextPage ? 'Loading…' : 'Show more'}
              </button>
            )}
          </>
        )}
      </div>
      {menuFor && (
        <MuteMenu
          item={menuFor}
          onClose={() => setMenuFor(null)}
          onToggleRead={() => {
            setRead.mutate(
              { id: menuFor.id, read: !menuFor.read },
              { onError: (error) => showToast(readError(error)) },
            )
            setMenuFor(null)
          }}
          onMutePerson={(handle) =>
            muteTarget(
              'person',
              handle,
              `Muted ${muteName(menuFor) ?? 'this person'}. You can unmute in settings.`,
            )
          }
          onMuteDeed={(deedId) =>
            muteTarget('deed', deedId, 'Notifications for this deed are off.')
          }
        />
      )}
      {toast}
    </FeedMain>
  )
}

function NotificationRow({
  item,
  onOpen,
  onMenu,
}: {
  item: NotificationItem
  onOpen: () => void
  onMenu: () => void
}) {
  const href = linkHref(item.link)
  const body = (
    <>
      <ActorStack item={item} />
      <span className="flex min-w-0 grow flex-col gap-0.5">
        <span
          className={`text-[15px] leading-snug text-slate-900 ${item.read ? '' : 'font-medium'}`}
        >
          {item.text}
        </span>
        <span
          className={`text-[13px] ${item.read ? 'text-slate-500' : 'font-semibold text-brand-700'}`}
        >
          <span aria-hidden>{relativeTime(item.updated_at)}</span>
          <span className="sr-only">
            {relativeTimeLong(item.updated_at)}
            {item.read ? '' : ', unread'}
          </span>
        </span>
      </span>
      {!item.read && (
        <span
          aria-hidden
          data-testid="unread-dot"
          className="mt-2 size-2.5 shrink-0 rounded-full bg-brand-600"
        />
      )}
    </>
  )
  const area = `flex min-w-0 grow items-start gap-3 py-3 pl-3.5 text-left`
  return (
    <div
      data-unread={item.read ? undefined : true}
      className={`flex items-start ${item.read ? 'bg-white' : 'bg-brand-50/70'}`}
    >
      {href ? (
        <Link to={href} onClick={onOpen} className={`${area} hover:bg-slate-50/60 ${focusRing}`}>
          {body}
        </Link>
      ) : (
        <div className={area}>{body}</div>
      )}
      <button
        type="button"
        onClick={onMenu}
        aria-label="More options for this notification"
        aria-haspopup="dialog"
        className={`m-1.5 flex size-11 shrink-0 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-800 ${focusRing}`}
      >
        <DotsIcon />
      </button>
    </div>
  )
}

/** Up to two actors' avatars, stacked like Facebook/Instagram, with a small kind badge. */
function ActorStack({ item }: { item: NotificationItem }) {
  const [first, second] = item.actors
  let faces: ReactNode
  if (!first) {
    faces = (
      <span
        aria-hidden
        className={`flex size-12 items-center justify-center rounded-full ${SYSTEM_TONE[item.category] ?? 'bg-brand-50 text-brand-700'}`}
      >
        {item.category === 'reviews' ? <ReviewIcon /> : <BellIcon />}
      </span>
    )
  } else if (!second) {
    faces = <Avatar user={first} size="row" />
  } else {
    faces = (
      <span aria-hidden className="relative block size-12">
        <Avatar user={second} size="sm" className="absolute top-0 left-0" />
        <Avatar user={first} size="sm" className="absolute right-0 bottom-0 ring-2 ring-white" />
      </span>
    )
  }
  const badge = KIND_BADGE[item.kind]
  return (
    <span className="relative shrink-0">
      {faces}
      {badge && (
        <span
          aria-hidden
          className={`absolute -right-1 -bottom-1 flex size-5.5 items-center justify-center rounded-full ring-2 ring-white ${badge.tone}`}
        >
          {badge.icon}
        </span>
      )}
    </span>
  )
}

const SYSTEM_TONE: Record<string, string> = {
  account: 'bg-amber-50 text-amber-700',
  reviews: 'bg-brand-50 text-brand-700',
  about_you: 'bg-emerald-50 text-emerald-700',
}

function SmallIcon({ d, fill = false }: { d: string; fill?: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-3"
      fill={fill ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  )
}

const CHECK = 'M5 12.5l4.5 4.5L19 7.5'
const KIND_BADGE: Record<string, { tone: string; icon: ReactNode }> = {
  like: {
    tone: 'bg-rose-600 text-white',
    icon: (
      <SmallIcon
        fill
        d="M12 20.5s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.7a4.3 4.3 0 0 1 7.5 2.8c0 5.4-7.5 10-7.5 10z"
      />
    ),
  },
  comment: { tone: 'bg-brand-600 text-white', icon: <SmallIcon d="M4 5h16v11H9l-5 4z" /> },
  reply: { tone: 'bg-brand-600 text-white', icon: <SmallIcon d="M4 5h16v11H9l-5 4z" /> },
  follow: {
    tone: 'bg-sky-600 text-white',
    icon: <SmallIcon d="M9 8a3 3 0 1 0 0-.01M3.5 19a5.5 5.5 0 0 1 11 0M18 8v6M15 11h6" />,
  },
  recognised: { tone: 'bg-emerald-600 text-white', icon: <SmallIcon d={CHECK} /> },
  deed_verified: { tone: 'bg-emerald-600 text-white', icon: <SmallIcon d={CHECK} /> },
  network: { tone: 'bg-emerald-600 text-white', icon: <SmallIcon d={CHECK} /> },
}

function GearIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-6"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  )
}

function ReviewIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-6"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 5h16v11H9l-5 4z" />
      <path d="M8.5 10.5h7" />
    </svg>
  )
}
