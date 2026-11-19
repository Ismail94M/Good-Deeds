/**
 * Parts shared by the admin review screens (photo reports now, comment reports in F19):
 * page frame, queue tabs and priority chips (docs/M3-DESIGN.md §6.3 "Admin").
 */

import type { ReactNode } from 'react'
import type { ReportPriority } from './adminApi'

export const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

export function AdminMain({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 pt-5 pb-12 lg:px-6 lg:pt-8">
      {children}
    </main>
  )
}

export function AdminTitle({ children }: { children: ReactNode }) {
  return (
    <h1 className="text-[26px] leading-tight font-bold tracking-tight text-slate-900 sm:text-[32px]">
      {children}
    </h1>
  )
}

export type ChipKind = 'photo' | 'comment'

const TONES: Record<ReportPriority, string> = {
  1: 'bg-red-100 text-red-800',
  2: 'bg-orange-100 text-orange-800',
  3: 'bg-slate-100 text-slate-700',
}

/**
 * Priority labels. Photos (Q44): Child red, Privacy/Harm orange, Other grey. Comments (Q38,
 * _interim_ labels): threats and personal details first, then hate, then spam and the rest.
 * The label carries the meaning, not the colour.
 */
const LABELS: Record<ChipKind, Record<ReportPriority, string>> = {
  photo: { 1: 'Child', 2: 'Privacy/Harm', 3: 'Other' },
  comment: { 1: 'Safety', 2: 'Hate', 3: 'Other' },
}

export function PriorityChip({
  priority,
  kind = 'photo',
}: {
  priority: ReportPriority
  kind?: ChipKind
}) {
  return (
    <span
      className={`inline-flex self-start rounded-full px-2 py-px text-xs font-bold ${TONES[priority]}`}
    >
      {LABELS[kind][priority]}
    </span>
  )
}
