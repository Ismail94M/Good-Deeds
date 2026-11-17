import { useId } from 'react'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/**
 * "How many people did they help? (optional)": − value + (F3 deed form, F6 edit form).
 * The value stays between 0 and `max`; the buttons are disabled at either end.
 */
export function PeopleStepper({
  value,
  onChange,
  max,
  label = 'How many people did they help?',
}: {
  value: number
  onChange: (value: number) => void
  /** `beneficiaries_max` from `/deeds/options`. */
  max: number
  label?: string
}) {
  const labelId = useId()
  const button = `flex size-12 items-center justify-center rounded-lg text-slate-800 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent ${focusRing}`
  return (
    <div className="space-y-2">
      <p id={labelId} className="font-semibold text-slate-900">
        {label} <span className="font-normal text-slate-600">(optional)</span>
      </p>
      <div
        role="group"
        aria-labelledby={labelId}
        className="inline-flex items-center rounded-lg border border-slate-300 bg-white"
      >
        <button
          type="button"
          aria-label="One fewer"
          disabled={value <= 0}
          onClick={() => onChange(Math.max(0, value - 1))}
          className={button}
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
          >
            <path d="M5 12h14" />
          </svg>
        </button>
        <output aria-live="polite" className="min-w-12 text-center text-lg font-bold">
          {value}
        </output>
        <button
          type="button"
          aria-label="One more"
          disabled={value >= max}
          onClick={() => onChange(Math.min(max, value + 1))}
          className={button}
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>
    </div>
  )
}
