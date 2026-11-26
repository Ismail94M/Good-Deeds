/** Small display parts of the M5 scoring admin screens. */

import type { ReactNode } from 'react'

/** "{n} / {max}" under a note box. */
export function CharCount({ id, value, max }: { id: string; value: string; max: number }) {
  return (
    <p id={id} className="text-right text-[13px] text-slate-600" aria-live="polite">
      {value.length} / {max}
    </p>
  )
}

export function Banner({
  tone,
  children,
}: {
  tone: 'amber' | 'blue' | 'green'
  children: ReactNode
}) {
  const tones = {
    amber: 'border-amber-200 bg-amber-50 text-amber-900',
    blue: 'border-brand-100 bg-brand-50 text-brand-900',
    green: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  }
  return (
    <p className={`rounded-xl border px-3.5 py-2.5 text-[15px] font-medium ${tones[tone]}`}>
      {children}
    </p>
  )
}
