/**
 * In-app notifications (docs/M6-DESIGN.md §3.2 N1–N7, D106–D115; backend/app/modules/notifications).
 * Every `text` is built by the server when the list is read; render it as given (§6.4).
 */

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query'
import { useContext } from 'react'
import { apiDelete, apiGet, apiPost, apiPut, queryString } from '../../lib/api'
import { AuthContext } from '../auth/authContext'
import type { Page } from '../deeds/deedsApi'

const seg = encodeURIComponent

export type NotificationCategory = 'about_you' | 'network' | 'activity' | 'account' | 'reviews'

export type NotificationLinkKind =
  | 'deed'
  | 'my_deed'
  | 'profile'
  | 'reviews'
  | 'home'
  | 'badges'
  | 'guidelines'
  | 'connections'
  | 'settings'

/** A `UserRef`; `handle` is null for an unavailable account (silhouette, no link). */
export type NotificationActor = {
  handle: string | null
  display_name: string
  avatar?: string | null
}

export type NotificationLink = { kind: NotificationLinkKind; target: string | null }

export type NotificationItem = {
  id: string
  kind: string
  category: NotificationCategory
  created_at: string
  updated_at: string
  read: boolean
  text: string
  /** Newest first, at most 3. */
  actors: NotificationActor[]
  actor_count: number
  /** Null when the deed is no longer available (D109). */
  link: NotificationLink | null
  can_mute: { person?: string; deed?: string }
}

export type NotificationsPage = Page<NotificationItem>

export type UnreadCount = { unread: number; new_since_seen: number }

export type CategoryPref = {
  id: NotificationCategory
  label: string
  description: string
  on: boolean
  locked: boolean
}

export type Preferences = {
  categories: CategoryPref[]
  mutes: { people: NotificationActor[]; deeds: { deed_id: string; title_text: string }[] }
}

export type MuteTarget = { type: 'person' | 'deed'; target: string }

export type NotificationsTab = 'all' | 'unread'

/** D114: the bell polls this often while the tab is visible (`unread_poll_seconds`, 60). */
export const UNREAD_POLL_MS = 60_000

export const notificationsApi = {
  list: (tab: NotificationsTab, cursor: string | null) =>
    apiGet<NotificationsPage>(
      `/notifications${queryString({ cursor, unread: tab === 'unread' ? 'true' : null })}`,
    ),
  unreadCount: () => apiGet<UnreadCount>('/notifications/unread-count'),
  seen: () => apiPost<void>('/notifications/seen'),
  read: (id: string) => apiPost<void>(`/notifications/${seg(id)}/read`),
  unread: (id: string) => apiPost<void>(`/notifications/${seg(id)}/unread`),
  readAll: () => apiPost<void>('/notifications/read-all'),
  preferences: () => apiGet<Preferences>('/notifications/preferences'),
  setPreference: (category: NotificationCategory, on: boolean) =>
    apiPut<Preferences>('/notifications/preferences', { category, on }),
  mute: (data: MuteTarget) => apiPut<void>('/notifications/mutes', data),
  unmute: (data: MuteTarget) => apiDelete('/notifications/mutes', data),
}

/** §6.4 query keys. */
export const notificationsKeys = {
  all: ['notifications'] as const,
  list: (tab: NotificationsTab) => ['notifications', 'list', tab] as const,
  lists: ['notifications', 'list'] as const,
  unread: ['notifications', 'unread'] as const,
  prefs: ['notifications', 'prefs'] as const,
}

/** The bell's count: on load, on focus and every minute while the tab is visible (D114). */
export function useUnreadCount() {
  const signedIn = Boolean(useContext(AuthContext)?.user)
  return useQuery({
    queryKey: notificationsKeys.unread,
    queryFn: notificationsApi.unreadCount,
    enabled: signedIn,
    retry: false,
    refetchOnWindowFocus: true,
    refetchInterval: UNREAD_POLL_MS,
    refetchIntervalInBackground: false,
  })
}

export function useNotifications(tab: NotificationsTab) {
  return useInfiniteQuery({
    queryKey: notificationsKeys.list(tab),
    queryFn: ({ pageParam }) => notificationsApi.list(tab, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    retry: false,
  })
}

type ListData = InfiniteData<NotificationsPage, string | null>

/** Mark one row read or unread, updating every cached list at once. */
export function useSetRead() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, read }: { id: string; read: boolean }) =>
      read ? notificationsApi.read(id) : notificationsApi.unread(id),
    onMutate: ({ id, read }) => {
      queryClient.setQueriesData<ListData>({ queryKey: notificationsKeys.lists }, (data) =>
        data
          ? {
              ...data,
              pages: data.pages.map((page) => ({
                ...page,
                items: page.items.map((item) => (item.id === id ? { ...item, read } : item)),
              })),
            }
          : data,
      )
    },
    onError: () => void queryClient.invalidateQueries({ queryKey: notificationsKeys.lists }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: notificationsKeys.unread })
      void queryClient.invalidateQueries({ queryKey: notificationsKeys.list('unread') })
    },
  })
}

export function useReadAll() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: notificationsApi.readAll,
    onSuccess: () => {
      queryClient.setQueriesData<ListData>({ queryKey: notificationsKeys.lists }, (data) =>
        data
          ? {
              ...data,
              pages: data.pages.map((page) => ({
                ...page,
                items: page.items.map((item) => ({ ...item, read: true })),
              })),
            }
          : data,
      )
      void queryClient.invalidateQueries({ queryKey: notificationsKeys.unread })
      void queryClient.invalidateQueries({ queryKey: notificationsKeys.list('unread') })
    },
  })
}

export function usePreferences() {
  return useQuery({
    queryKey: notificationsKeys.prefs,
    queryFn: notificationsApi.preferences,
    retry: false,
  })
}

/** Mute or unmute a person or a deed (D113). Mutes are silent. */
export function useMute() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ mute, ...target }: MuteTarget & { mute: boolean }) =>
      mute ? notificationsApi.mute(target) : notificationsApi.unmute(target),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: notificationsKeys.prefs }),
  })
}
