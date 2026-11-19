import { useDeedOptions } from '../deeds/deedQueries'
import { categoryLabel } from '../deeds/deedText'
import { adminApi, adminKeys, type PhotoReportItem } from './adminApi'
import { useReasonLabel } from './reasonLabels'
import { QueueRow, ReportQueue } from './ReportQueue'

const OPEN_NOTE =
  'Children first, then privacy and harmful images, then the rest; oldest first within each. Photos stay visible until you decide.'

/**
 * `/admin/photo-reports?status=open|resolved` (photos canvas §3, D-AdminQueue / M-AdminQueue;
 * docs/M3-DESIGN.md §6.3): the queue in the API's order (priority, then oldest), with a priority
 * chip (Q44), the top reason, the deed, the report count and the oldest report's age.
 */
export function PhotoReportsPage() {
  return (
    <ReportQueue
      title="Photo reports"
      openNote={OPEN_NOTE}
      noun="photo"
      queryKey={adminKeys.photoReports}
      fetchPage={(status, cursor) => adminApi.photoReports({ status, cursor })}
      renderRow={(item) => <PhotoRow item={item} />}
    />
  )
}

function PhotoRow({ item }: { item: PhotoReportItem }) {
  const reasonLabel = useReasonLabel()
  const categories = useDeedOptions().data?.categories
  return (
    <QueueRow
      thumb={
        <img
          src={item.photo_thumb_url}
          alt=""
          className="size-14 shrink-0 rounded-[10px] bg-slate-200 object-cover"
        />
      }
      priority={item.priority}
      kind="photo"
      reason={reasonLabel(item.top_reason)}
      context={`${item.deed.doer.display_name} · ${categoryLabel(categories, item.deed.category)} · ${item.deed.area_name}`}
      reportCount={item.report_count}
      oldestReportAt={item.oldest_report_at}
      href={`/admin/photo-reports/${encodeURIComponent(item.case_id)}`}
      reviewLabel={`Review the report on ${item.deed.doer.display_name}’s photo`}
    />
  )
}
