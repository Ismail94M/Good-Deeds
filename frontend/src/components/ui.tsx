import { useId, useState, type ComponentProps, type ReactNode } from 'react'
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
        <h1 className="text-2xl font-bold tracking-tight text-balance text-slate-900 sm:text-3xl">
          {title}
        </h1>
        {intro && <p className="text-slate-600">{intro}</p>}
      </header>
      {children}
    </main>
  )
}

type ButtonProps = ComponentProps<'button'> & { variant?: 'primary' | 'secondary' | 'danger' }

const variants = {
  primary: 'bg-brand-700 text-white shadow-sm hover:bg-brand-900',
  secondary: 'border border-slate-200 bg-white text-slate-800 hover:bg-slate-50',
  danger: 'bg-red-700 text-white hover:bg-red-800',
}

export function Button({ variant = 'primary', className = '', type, ...props }: ButtonProps) {
  return (
    <button
      type={type ?? 'button'}
      className={`inline-flex min-h-11 items-center justify-center rounded-lg px-5 font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${focusRing} ${variants[variant]} ${className}`}
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
      className={`inline-flex min-h-11 items-center justify-center rounded-lg px-5 font-semibold transition-colors ${focusRing} ${variants[variant]} ${className}`}
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
  /** Control shown inside the right edge of the input, e.g. a show-password toggle. */
  trailing?: ReactNode
}

/** Shared look for text inputs, selects and textareas. */
export const inputClass = `block min-h-11 w-full rounded-lg border bg-white px-3 text-base text-slate-900 placeholder:text-slate-400 ${focusRing}`

export function TextField({
  label,
  hint,
  error,
  id,
  trailing,
  className = '',
  ...props
}: FieldProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  const hintId = `${inputId}-hint`
  const errorId = `${inputId}-error`
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ')
  return (
    <div className="space-y-1">
      <label htmlFor={inputId} className="block text-sm font-medium text-slate-800">
        {label}
      </label>
      <div className="relative">
        <input
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          className={`${inputClass} ${error ? 'border-red-600' : 'border-slate-200'} ${trailing ? 'pr-12' : ''} ${className}`}
          {...props}
        />
        {trailing && (
          <div className="absolute inset-y-0 right-0 flex items-center pr-1">{trailing}</div>
        )}
      </div>
      {hint && (
        <p id={hintId} className="text-sm text-slate-500">
          {hint}
        </p>
      )}
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </div>
  )
}

/** Password input with a show/hide toggle, as in the Figma sign-in design. */
export function PasswordField(props: Omit<FieldProps, 'type' | 'trailing'>) {
  const [visible, setVisible] = useState(false)
  return (
    <TextField
      {...props}
      type={visible ? 'text' : 'password'}
      trailing={
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? 'Hide password' : 'Show password'}
          aria-pressed={visible}
          className={`flex size-10 items-center justify-center rounded-md text-slate-500 hover:text-slate-800 ${focusRing}`}
        >
          <EyeIcon off={!visible} />
        </button>
      }
    />
  )
}

function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
      {off && <path d="M4 4l16 16" />}
    </svg>
  )
}

/** Brand mark: blue rounded square with a heart. */
export function LogoMark({ className = 'size-8' }: { className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 32 32" className={className}>
      <rect width="32" height="32" rx="8" className="fill-brand-700" />
      <path
        fill="#fff"
        d="M16 24.5s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 16 11.7a4.3 4.3 0 0 1 7.5 2.8c0 5.4-7.5 10-7.5 10z"
      />
    </svg>
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
          className={`mt-0.5 size-5 shrink-0 accent-brand-700 ${focusRing}`}
          {...props}
        />
        <label htmlFor={inputId} className="text-slate-800">
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
    <p role="status" className="px-4 py-10 text-center text-slate-600">
      {label}
    </p>
  )
}
