import { useDeedOptions } from '../deeds/deedQueries'
import { categoryLabel } from '../deeds/deedText'
import { adminApi, adminKeys, type CommentReportItem } from './adminApi'
import { useCommentReasons } from './reasonLabels'
import { QueueRow, ReportQueue } from './ReportQueue'

const OPEN_NOTE =
  'Threats and personal details first, then hate, then the rest; oldest first within each. Comments stay up until you decide.'

/**
 * `/admin/comment-reports?status=open|resolved` (Q38, _interim_ layout reusing the photo queue,
 * M3-DESIGN §6.3): priority chip, top reason, an 80-character excerpt, "{author} on {doer}'s
 * deed", the report count and the oldest report's age.
 */
export function CommentReportsPage() {
  return (
    <ReportQueue
      title="Comment reports"
      openNote={OPEN_NOTE}
      noun="comment"
      queryKey={adminKeys.commentReports}
      fetchPage={(status, cursor) => adminApi.commentReports({ status, cursor })}
      renderRow={(item) => <CommentRow item={item} />}
    />
  )
}

function CommentRow({ item }: { item: CommentReportItem }) {
  const reasons = useCommentReasons()
  const categories = useDeedOptions().data?.categories
  return (
    <QueueRow
      priority={item.priority}
      kind="comment"
      reason={reasons.label(item.top_reason)}
      context={
        <>
          <span className="block break-words text-slate-700 italic">“{item.comment_excerpt}”</span>
          {item.author.display_name} on {item.deed.doer.display_name}’s deed ·{' '}
          {categoryLabel(categories, item.deed.category)} · {item.deed.area_name}
        </>
      }
      reportCount={item.report_count}
      oldestReportAt={item.oldest_report_at}
      href={`/admin/comment-reports/${encodeURIComponent(item.case_id)}`}
      reviewLabel={`Review the report on ${item.author.display_name}’s comment`}
    />
  )
}
