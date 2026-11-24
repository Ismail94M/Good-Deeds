import { useId, type ReactNode } from 'react'
import { FieldError } from '../../components/ui'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/**
 * Description textarea with a "{len} / {max}" counter (F3 deed form, F6 edit and self-log
 * forms). Typing beyond `max` is blocked by `maxLength`.
 */
export function DescriptionField({
  label = 'What did they do?',
  value,
  onChange,
  max,
  hint,
  error,
  placeholder,
  name = 'description',
}: {
  label?: string
  value: string
  onChange: (value: string) => void
  max: number
  hint?: ReactNode
  error?: string | null
  placeholder?: string
  name?: string
}) {
  const id = useId()
  const hintId = `${id}-hint`
  const countId = `${id}-count`
  const errorId = `${id}-error`
  const describedBy = [hint ? hintId : null, countId, error ? errorId : null]
    .filter(Boolean)
    .join(' ')
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block font-semibold text-slate-900">
        {label}
      </label>
      <textarea
        id={id}
        name={name}
        rows={3}
        maxLength={max}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`block min-h-21 w-full resize-y rounded-lg bg-white px-3.5 py-2.5 text-base text-slate-900 placeholder:text-slate-400 ${focusRing} ${
          error ? 'border-2 border-red-700' : 'border border-slate-300'
        }`}
      />
      <div className="flex justify-between gap-3 text-sm">
        {hint ? (
          <p id={hintId} className="text-slate-600">
            {hint}
          </p>
        ) : (
          <span />
        )}
        <p className={`shrink-0 font-semibold ${error ? 'text-red-700' : 'text-slate-600'}`}>
          <span aria-hidden>{`${value.length} / ${max}`}</span>
          <span id={countId} className="sr-only">
            {`${value.length} of ${max} characters`}
          </span>
        </p>
      </div>
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </div>
  )
}
