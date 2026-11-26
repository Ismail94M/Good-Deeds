import { Link } from 'react-router'
import { focusRing } from '../deeds/deedUi'
import { noticeForDeed, useMyReviews } from './reviewsApi'

/**
 * M5 D101, §6.2: in My deeds a rejected deed reads "Removed after review" and links to the
 * decision notice that lists it under Profile → Reviews (or to Reviews while it can't be found).
 */
export function RemovedAfterReview({ deedId }: { deedId: string }) {
  const reviews = useMyReviews()
  const notice = noticeForDeed(reviews.data, deedId)
  const to = notice ? `/me/reviews#notice-${encodeURIComponent(notice.id)}` : '/me/reviews'
  return (
    <Link
      to={to}
      className={`inline-flex min-h-8 items-center rounded-full border border-red-200 bg-red-50 px-2.5 py-0.5 text-[13px] font-semibold text-red-700 underline underline-offset-2 hover:bg-red-100 ${focusRing}`}
    >
      Removed after review
    </Link>
  )
}
