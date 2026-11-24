/**
 * Leaderboard levels (D65): Area (ward), City, State, Country. Every level always has a tab; it
 * is open when the `leaderboard` config enables it (`GET /scoring/options` → `levels[].enabled`)
 * and shown as locked otherwise. Opening a level later is a config change only: no UI change.
 */
import type { RegionKind, ScoringOptions } from './scoringApi'

/** Tab order, smallest place first. */
export const LEVEL_ORDER: readonly RegionKind[] = ['ward', 'city', 'state', 'country']

/** What people see. A ward is called an "Area" in the app. */
export const LEVEL_LABEL: Record<RegionKind, string> = {
  ward: 'Area',
  city: 'City',
  state: 'State',
  country: 'Country',
}

export const LEVEL_NOUN: Record<
  RegionKind,
  { one: string; plural: string; article: 'a' | 'an'; example: string }
> = {
  ward: { one: 'area', plural: 'areas', article: 'an', example: 'e.g. Koramangala' },
  city: { one: 'city', plural: 'cities', article: 'a', example: 'e.g. Bangalore' },
  state: { one: 'state', plural: 'states', article: 'a', example: 'e.g. Karnataka' },
  country: { one: 'country', plural: 'countries', article: 'a', example: 'e.g. India' },
}

/** The level a bare `/leaderboard` opens on. */
export const DEFAULT_LEVEL: RegionKind = 'ward'

/** Until the options load, only the default level is treated as open (nothing locked opens). */
export function openLevels(options: ScoringOptions | undefined): Set<RegionKind> {
  if (!options) return new Set([DEFAULT_LEVEL])
  return new Set(options.levels.filter((level) => level.enabled).map((level) => level.kind))
}

/** `?level=` from the URL, if it names a level. */
export function parseLevel(value: string | null): RegionKind | null {
  return value !== null && (LEVEL_ORDER as readonly string[]).includes(value)
    ? (value as RegionKind)
    : null
}

/** Link to a level's tab; the default level is the bare `/leaderboard`. */
export function levelHref(kind: RegionKind, month: string | null): string {
  const params = new URLSearchParams()
  if (kind !== DEFAULT_LEVEL) params.set('level', kind)
  if (month) params.set('month', month)
  const query = params.toString()
  return query ? `/leaderboard?${query}` : '/leaderboard'
}

/** "City, State and Country leaderboards are coming soon." */
export function comingSoonText(kinds: RegionKind[]): string {
  const names = kinds.map((kind) => LEVEL_LABEL[kind])
  const list =
    names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
  return `${list} leaderboards are coming soon.`
}
