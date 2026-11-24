import type { Tier } from './scoringApi'

/**
 * The public tier (Seed / Sprout / Tree / Forest, M4-DESIGN §6.3, D69). The label is the API's
 * display text; the word "Tier" is in the text too, so colour is never the only signal.
 */
export function TierChip({ tier }: { tier: Tier }) {
  return (
    <span
      data-tier={tier.slug}
      className="inline-flex min-h-7 items-center gap-1.5 rounded-full bg-emerald-50 px-3 text-sm font-semibold text-emerald-800 ring-1 ring-emerald-200"
    >
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        className="size-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 21v-8M12 13c0-4 3-7 8-7 0 4-3 7-8 7zM12 15c0-3-2.5-5.5-7-5.5 0 3 2.5 5.5 7 5.5z" />
      </svg>
      <span className="sr-only">Tier: </span>
      {tier.label}
    </span>
  )
}
