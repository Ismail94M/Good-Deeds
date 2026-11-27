/**
 * Member reviews (docs/M5-DESIGN.md §3.3b M1–M4, D99–D101; backend/app/modules/fraud/schemas.py):
 * explanation requests and decision notices about the signed-in person's own deeds. Every text
 * (`outcome_text`, `reason_text`, `effect_text`, appeal `outcome_text`) is made by the server in
 * member wording; render it as given, never re-word it here.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { retryUnlessClientError } from '../feed/feedQueries'

const seg = encodeURIComponent

export type ReviewPerson = { handle: string; display_name: string; avatar?: string | null }

/** One of my own deeds, with the other person on that deed. */
export type MyDeedRef = {
  deed_id: string
  role: 'doer' | 'reporter'
  category_label: string
  area_name: string
  /** `YYYY-MM-DD`, the IST day. */
  occurred_on: string
  other_person: ReviewPerson
}

export type MyExplanation = {
  id: string
  requested_at: string
  reply_due_at: string
  replied_at: string | null
  reply: string | null
  status: 'waiting' | 'replied' | 'closed'
  outcome_text: string | null
  deeds: MyDeedRef[]
}

export type MyAppeal = {
  created_at: string
  reason: string
  outcome: 'upheld' | 'overturned' | null
  outcome_text: string | null
  decided_at: string | null
}

export type MyNotice = {
  id: string
  created_at: string
  reason_text: string
  items: { deed: MyDeedRef; effect_text: string }[]
  appeal_until: string
  can_appeal: boolean
  appeal: MyAppeal | null
  final: boolean
}

export type ReviewLimits = {
  reply_max_chars: number
  appeal_reason_min_chars: number
  appeal_reason_max_chars: number
}

export type MyReviews = {
  explanations: MyExplanation[]
  notices: MyNotice[]
  unread: number
  limits: ReviewLimits
}

export const reviewsApi = {
  list: () => apiGet<MyReviews>('/reviews'),
  reply: (id: string, reply: string) =>
    apiPost<MyExplanation>(`/reviews/explanations/${seg(id)}/reply`, { reply }),
  appeal: (id: string, reason: string) =>
    apiPost<MyNotice>(`/reviews/notices/${seg(id)}/appeal`, { reason }),
  seen: () => apiPost<void>('/reviews/seen'),
}

export const reviewsKeys = {
  all: ['reviews'] as const,
}

/** `GET /reviews` (M1). Signed-in only; also feeds the Profile dot and My deeds links. */
export function useMyReviews(enabled = true) {
  const { user } = useAuth()
  return useQuery({
    queryKey: reviewsKeys.all,
    queryFn: reviewsApi.list,
    retry: retryUnlessClientError,
    staleTime: 60_000,
    enabled: enabled && !!user,
  })
}

/** True while the person has review items they haven't seen (the Profile dot). */
export function useReviewsUnread(): boolean {
  const reviews = useMyReviews()
  return (reviews.data?.unread ?? 0) > 0
}

/** The notice that lists this deed, if any (My deeds "Removed after review" link). */
export function noticeForDeed(reviews: MyReviews | undefined, deedId: string): MyNotice | null {
  return reviews?.notices.find((n) => n.items.some((i) => i.deed.deed_id === deedId)) ?? null
}

/** Patch one item into the cached page after a write. */
export function useUpdateReviews() {
  const queryClient = useQueryClient()
  return (update: (current: MyReviews) => MyReviews) =>
    queryClient.setQueryData<MyReviews>(reviewsKeys.all, (current) =>
      current ? update(current) : current,
    )
}
