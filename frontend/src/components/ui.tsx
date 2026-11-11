import { useId, type ComponentProps, type ReactNode } from 'react'
import { Link, type LinkProps } from 'react-router'

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/** Page wrapper with a title. */
export function Page({
  title,
  intro,
  children,
}: {
  title: string
  intro?: ReactNode
  children: ReactNode
}) {
  return (
    <main className="mx-auto w-full max-w-xl space-y-6 px-4 py-6 sm:py-10">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold text-balance text-stone-900 sm:text-3xl">{title}</h1>
        {intro && <p className="text-stone-600">{intro}</p>}
      </header>
      {children}
    </main>
  )
}

type ButtonProps = ComponentProps<'button'> & { variant?: 'primary' | 'secondary' | 'danger' }

const variants = {
  primary: 'bg-brand-700 text-white hover:bg-brand-900',
  secondary: 'border border-stone-300 bg-white text-stone-800 hover:bg-stone-100',
  danger: 'bg-red-700 text-white hover:bg-red-800',
}

export function Button({ variant = 'primary', className = '', type, ...props }: ButtonProps) {
  return (
    <button
      type={type ?? 'button'}
      className={`inline-flex min-h-11 items-center justify-center rounded-full px-5 font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${focusRing} ${variants[variant]} ${className}`}
      {...props}
    />
  )
}

export function LinkButton({
  variant = 'primary',
  className = '',
  ...props
}: LinkProps & { variant?: 'primary' | 'secondary' }) {
  return (
    <Link
      className={`inline-flex min-h-11 items-center justify-center rounded-full px-5 font-semibold transition-colors ${focusRing} ${variants[variant]} ${className}`}
      {...props}
    />
  )
}

export function TextLink({ className = '', ...props }: LinkProps) {
  return (
    <Link
      className={`font-medium text-brand-700 underline underline-offset-2 hover:text-brand-900 ${focusRing} ${className}`}
      {...props}
    />
  )
}

type FieldProps = ComponentProps<'input'> & {
  label: string
  hint?: ReactNode
  error?: string | null
}

export function TextField({ label, hint, error, id, className = '', ...props }: FieldProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  const hintId = `${inputId}-hint`
  const errorId = `${inputId}-error`
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ')
  return (
    <div className="space-y-1">
      <label htmlFor={inputId} className="block font-medium text-stone-800">
        {label}
      </label>
      <input
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={`block min-h-11 w-full rounded-lg border bg-white px-3 text-base text-stone-900 ${error ? 'border-red-600' : 'border-stone-300'} ${focusRing} ${className}`}
        {...props}
      />
      {hint && (
        <p id={hintId} className="text-sm text-stone-500">
          {hint}
        </p>
      )}
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </div>
  )
}

export function FieldError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="text-sm font-medium text-red-700">
      <span aria-hidden>⚠ </span>
      {children}
    </p>
  )
}

export function Checkbox({
  label,
  error,
  id,
  ...props
}: Omit<ComponentProps<'input'>, 'type'> & { label: ReactNode; error?: string | null }) {
  const autoId = useId()
  const inputId = id ?? autoId
  const errorId = `${inputId}-error`
  return (
    <div className="space-y-1">
      <div className="flex items-start gap-3">
        <input
          id={inputId}
          type="checkbox"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={`mt-0.5 size-6 shrink-0 accent-brand-700 ${focusRing}`}
          {...props}
        />
        <label htmlFor={inputId} className="text-stone-800">
          {label}
        </label>
      </div>
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </div>
  )
}

/** Error banner for a failed action. */
export function ErrorAlert({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-800">
      {children}
    </p>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p role="status" className="px-4 py-10 text-center text-stone-600">
      {label}
    </p>
  )
}
