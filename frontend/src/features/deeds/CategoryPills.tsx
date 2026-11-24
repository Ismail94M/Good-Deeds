import { useId } from 'react'
import { FieldError } from '../../components/ui'
import type { Category } from './deedsApi'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/**
 * "What kind of deed?": one pill per category from `/deeds/options`, one selected at a time
 * (F3 deed form, F6 edit and self-log forms). Pills are toggle buttons with `aria-pressed`.
 */
export function CategoryPills({
  categories,
  value,
  onChange,
  legend = 'What kind of deed?',
  error,
  disabled = false,
}: {
  categories: Category[]
  /** The selected `slug`, or null when none is chosen yet. */
  value: string | null
  onChange: (slug: string) => void
  legend?: string
  error?: string | null
  disabled?: boolean
}) {
  const errorId = `${useId()}-error`
  return (
    <fieldset className="min-w-0" aria-describedby={error ? errorId : undefined}>
      <legend className="mb-2.5 font-semibold text-slate-900">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {categories.map((category) => {
          const selected = category.slug === value
          return (
            <button
              key={category.slug}
              type="button"
              aria-pressed={selected}
              disabled={disabled}
              onClick={() => onChange(category.slug)}
              className={`min-h-11 rounded-full border px-3.5 text-[15px] transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${focusRing} ${
                selected
                  ? 'border-brand-700 bg-brand-700 font-semibold text-white'
                  : `bg-white font-medium text-slate-800 hover:bg-slate-50 ${
                      error ? 'border-red-600' : 'border-slate-300'
                    }`
              }`}
            >
              {category.label}
            </button>
          )
        })}
      </div>
      {error && (
        <div className="mt-2">
          <FieldError id={errorId}>{error}</FieldError>
        </div>
      )}
    </fieldset>
  )
}
