import { useId, useState } from 'react'
import { Link } from 'react-router'
import { inputClass } from '../../components/ui'
import { focusRing } from '../feed/feedUi'
import { LEVEL_NOUN } from './levels'
import type { RegionKind } from './scoringApi'
import { useRegionSearch } from './scoringQueries'

/**
 * "Find an area" (M4-DESIGN §6.3, §3.3 #7): searches the regions of one level by name once 2+
 * letters are typed and lists them as links to their board. Works for any level (`kind`), so a
 * level that is switched on later gets its search without UI changes. `month` (`YYYY-MM`) is
 * carried over when set.
 */
export function RegionPicker({ kind, month }: { kind: RegionKind; month: string | null }) {
  const [q, setQ] = useState('')
  const inputId = useId()
  const statusId = useId()
  const search = useRegionSearch(q, kind)
  const term = q.trim()
  const results = term.length >= 2 ? (search.data ?? []) : []
  const suffix = month ? `?month=${month}` : ''
  const noun = LEVEL_NOUN[kind]

  let status = ''
  if (term.length > 0 && term.length < 2) status = 'Type at least 2 letters.'
  else if (term.length >= 2 && search.isPending) status = 'Searching…'
  else if (term.length >= 2 && search.isError) status = 'Search didn’t work. Please try again.'
  else if (term.length >= 2 && search.data && search.data.length === 0) {
    status = `No ${noun.plural} match that name. Try another spelling.`
  }

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={inputId} className="font-semibold text-slate-900">
        Find {noun.article} {noun.one}
      </label>
      <input
        id={inputId}
        type="search"
        value={q}
        onChange={(event) => setQ(event.target.value)}
        placeholder={noun.example}
        autoComplete="off"
        aria-describedby={statusId}
        className={`${inputClass} border-slate-300`}
      />
      <p id={statusId} role="status" className="text-sm text-slate-600 empty:hidden">
        {status}
      </p>
      {results.length > 0 && (
        <ul
          aria-label={noun.plural.charAt(0).toUpperCase() + noun.plural.slice(1)}
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
