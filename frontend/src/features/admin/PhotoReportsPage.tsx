import { useSearchParams } from 'react-router'
import { useDeedOptions } from '../deeds/deedQueries'
import { categoryLabel } from '../deeds/deedText'
import { Avatar } from '../media/avatar/Avatar'
import { adminApi, adminKeys, type PhotoPurpose, type PhotoReportItem } from './adminApi'
import { focusRing } from './adminUi'
import { useReasonLabel } from './reasonLabels'
import { QueueRow, ReportQueue } from './ReportQueue'

const OPEN_NOTE =
  'Children first, then privacy, impersonation and harmful images, then the rest; oldest first within each. Photos stay visible until you decide.'

const FILTERS: { id: PhotoPurpose | null; label: string }[] = [
  { id: null, label: 'All' },
  { id: 'deed', label: 'Deed photos' },
  { id: 'avatar', label: 'Profile pictures' },
]

function readPurpose(value: string | null): PhotoPurpose | null {
  return value === 'deed' || value === 'avatar' ? value : null
}

/**
 * `/admin/photo-reports?status=open|resolved&purpose=deed|avatar` (photos canvas §3; M3-DESIGN
 * §6.3; M6 D121): the queue in the API's order (priority, then oldest), with a priority chip
 * (Q44), the top reason, the deed or the picture's owner, the report count and the oldest
 * report's age. M6 adds the "Profile pictures" filter.
 */
export function PhotoReportsPage() {
  const [params, setParams] = useSearchParams()
  const purpose = readPurpose(params.get('purpose'))
  return (
    <ReportQueue
      title="Photo reports"
      openNote={OPEN_NOTE}
      noun={purpose === 'avatar' ? 'profile picture' : 'photo'}
      queryKey={(status) => adminKeys.photoReports(status, purpose)}
      fetchPage={(status, cursor) => adminApi.photoReports({ status, cursor, purpose })}
      renderRow={(item) => <PhotoRow item={item} />}
      toolbar={
        <div role="group" aria-label="Show" className="flex flex-wrap gap-2">
          {FILTERS.map((filter) => {
            const active = filter.id === purpose
            return (
              <button
                key={filter.label}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  const next = new URLSearchParams(params)
                  if (filter.id) next.set('purpose', filter.id)
                  else next.delete('purpose')
                  setParams(next, { replace: true })
                }}
                className={`min-h-11 rounded-full border px-4 text-sm font-semibold ${focusRing} ${
                  active
                    ? 'border-brand-700 bg-brand-700 text-white'
                    : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                {filter.label}
              </button>
            )
          })}
        </div>
      }
    />
  )
}

function PhotoRow({ item }: { item: PhotoReportItem }) {
  const reasonLabel = useReasonLabel()
  const categories = useDeedOptions().data?.categories
  const href = `/admin/photo-reports/${encodeURIComponent(item.case_id)}`
  const thumb = item.photo_thumb_url ? (
    <img
      src={item.photo_thumb_url}
      alt=""
      className={`size-14 shrink-0 bg-slate-200 object-cover ${
        item.purpose === 'avatar' ? 'rounded-full' : 'rounded-[10px]'
      }`}
    />
  ) : (
    <span aria-hidden className="size-14 shrink-0 rounded-[10px] bg-slate-200" />
  )

  if (item.purpose === 'avatar' || !item.deed) {
    const owner = item.owner
    const name = owner?.display_name ?? 'Unavailable account'
    return (
      <QueueRow
        thumb={thumb}
        priority={item.priority}
        kind="photo"
        reason={reasonLabel(item.top_reason)}
        context={
          <span className="flex items-center gap-2">
            {owner && <Avatar user={owner} size="xs" />}
            <span>
              Profile picture · {name}
              {owner?.handle && (
                <span className="font-normal text-slate-600"> @{owner.handle}</span>
              )}
            </span>
          </span>
        }
        reportCount={item.report_count}
        oldestReportAt={item.oldest_report_at}
        href={href}
        reviewLabel={`Review the report on ${name}’s profile picture`}
      />
    )
  }

  const deed = item.deed
  return (
    <QueueRow
      thumb={thumb}
      priority={item.priority}
      kind="photo"
      reason={reasonLabel(item.top_reason)}
      context={
        <span className="flex items-center gap-2">
          <Avatar user={deed.doer} size="xs" />
          <span>
            {deed.doer.display_name} · {categoryLabel(categories, deed.category)} · {deed.area_name}
          </span>
        </span>
      }
      reportCount={item.report_count}
      oldestReportAt={item.oldest_report_at}
      href={href}
      reviewLabel={`Review the report on ${deed.doer.display_name}’s photo`}
    />
  )
}
