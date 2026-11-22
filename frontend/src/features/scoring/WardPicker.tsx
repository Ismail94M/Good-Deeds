import { useId, useState } from 'react'
import { Link } from 'react-router'
import { inputClass } from '../../components/ui'
import { focusRing } from '../feed/feedUi'
import { useRegionSearch } from './scoringQueries'

/**
 * "Find a ward" (M4-DESIGN §6.3, §3.3 #7): searches wards by name once 2+ letters are typed and
 * lists them as links to their board. `month` (`YYYY-MM`) is carried over when set.
 */
export function WardPicker({ month }: { month: string | null }) {
  const [q, setQ] = useState('')
  const inputId = useId()
  const statusId = useId()
  const search = useRegionSearch(q)
  const term = q.trim()
  const results = term.length >= 2 ? (search.data ?? []) : []
  const suffix = month ? `?month=${month}` : ''

  let status = ''
  if (term.length > 0 && term.length < 2) status = 'Type at least 2 letters.'
  else if (term.length >= 2 && search.isPending) status = 'Searching…'
  else if (term.length >= 2 && search.isError) status = 'Search didn’t work. Please try again.'
  else if (term.length >= 2 && search.data && search.data.length === 0) {
    status = 'No wards match that name. Try another spelling.'
  }

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={inputId} className="font-semibold text-slate-900">
        Find a ward
      </label>
      <input
        id={inputId}
        type="search"
        value={q}
        onChange={(event) => setQ(event.target.value)}
        placeholder="e.g. Koramangala"
        autoComplete="off"
        aria-describedby={statusId}
        className={`${inputClass} border-slate-300`}
      />
      <p id={statusId} role="status" className="text-sm text-slate-600 empty:hidden">
        {status}
      </p>
      {results.length > 0 && (
        <ul
          aria-label="Wards"
          className="flex flex-col rounded-xl border border-slate-200 bg-white"
        >
          {results.map((region) => (
            <li key={region.code} className="border-b border-slate-100 last:border-b-0">
              <Link
                to={`/leaderboard/${encodeURIComponent(region.code)}${suffix}`}
                onClick={() => setQ('')}
                className={`flex min-h-11 items-center px-3.5 text-slate-900 hover:bg-slate-50 ${focusRing}`}
              >
                {region.name}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
