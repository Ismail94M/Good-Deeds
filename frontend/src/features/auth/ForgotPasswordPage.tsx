import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { Button, ErrorAlert, PasswordField, TextField, TextLink } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useCountdown } from '../../lib/useCountdown'
import { AuthShell } from './AuthShell'
import { identityApi } from './identityApi'
import { PhoneField } from './PhoneField'
import {
  collectErrors,
  maskPhone,
  PASSWORD_HINT,
  validatePassword,
  validatePasswordConfirm,
  validatePhone,
} from './validation'

/**
 * Same wait as phone verification (`identity.otp_resend_seconds`, 30 s by default). The forgot
 * endpoint doesn't return it, so this is the fallback when `resend_after_seconds` is missing.
 */
const RESEND_SECONDS = 30

export const INVALID_CODE_MESSAGE = 'That code is wrong or has expired. Check it or send a new one.'

/** Router state that tells the sign-in page to show the "password changed" notice. */
export type PasswordResetState = { passwordReset: true }

type ResetField = 'code' | 'password' | 'confirm'

/**
 * Forgot password (PO decision 2026-11-17). The phone, code and passwords live only in this
 * component's state: never in the URL, router state or storage.
 */
// The API answers a wrong, expired or used reset code with `otp_incorrect`.
const RESET_CODE_ERRORS = new Set(['otp_incorrect', 'invalid_code'])

export function ForgotPasswordPage() {
  const navigate = useNavigate()
  const [phone, setPhone] = useState('')
  const [phoneError, setPhoneError] = useState<string | null>(null)
  const [resendAt, setResendAt] = useState<number | null>(null)
  const [step, setStep] = useState<'phone' | 'reset'>('phone')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<Partial<Record<ResetField, string>>>({})
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const resendIn = useCountdown(resendAt)

  async function sendCode(): Promise<boolean> {
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      const result = await identityApi.passwordForgot(phone.trim())
      const wait = result.resend_after_seconds ?? RESEND_SECONDS
      setResendAt(Date.now() + wait * 1000)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.fieldErrors.phone) setPhoneError(err.fieldErrors.phone)
      setError(errorMessage(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault()
    const invalid = validatePhone(phone)
    setPhoneError(invalid)
    if (invalid) {
      setError(invalid)
      return
    }
    if (await sendCode()) {
      setCode('')
      setStep('reset')
    }
  }

  async function handleResend() {
    if (await sendCode()) {
      setCode('')
      setErrors({})
      setNotice('We sent a new code. It replaces the last one.')
    }
  }

  function clearError(field: ResetField) {
    setErrors((current) => {
      const next = { ...current }
      delete next[field]
      return next
    })
  }

  function changeNumber() {
    setStep('phone')
    setCode('')
    setPassword('')
    setConfirm('')
    setErrors({})
    setError(null)
    setNotice(null)
    setResendAt(null)
  }

  async function handleReset(event: FormEvent) {
    event.preventDefault()
    setNotice(null)
    const found = collectErrors<ResetField>({
      code: /^\d{6}$/.test(code) ? null : 'Please enter the 6-digit code from the SMS.',
      password: validatePassword(password),
      confirm: validatePasswordConfirm(password, confirm),
    })
    setErrors(found)
    if (Object.keys(found).length > 0) {
      setError('Please fix the highlighted details.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      await identityApi.passwordReset({ phone: phone.trim(), code, new_password: password })
      const state: PasswordResetState = { passwordReset: true }
      navigate('/login', { replace: true, state })
    } catch (err) {
      setBusy(false)
      if (err instanceof ApiError && RESET_CODE_ERRORS.has(err.code ?? '')) {
        setErrors({ code: INVALID_CODE_MESSAGE })
        setError(INVALID_CODE_MESSAGE)
        return
      }
      if (err instanceof ApiError) {
        const fieldPassword = err.fieldErrors.new_password ?? err.fieldErrors.password
        const fieldCode = err.fieldErrors.code
        if (fieldPassword || fieldCode) {
          setErrors({
            ...(fieldPassword ? { password: fieldPassword } : {}),
            ...(fieldCode ? { code: fieldCode } : {}),
          })
        }
      }
      setError(errorMessage(err))
    }
  }

  if (step === 'phone') {
    return (
      <AuthShell
        title="Reset your password"
        intro="Enter the mobile number on your Good Deeds account. We'll text you a 6-digit code."
      >
        <form noValidate onSubmit={(e) => void handleSend(e)} className="space-y-5">
          {error && <ErrorAlert>{error}</ErrorAlert>}
          <PhoneField
            label="Mobile number"
            required
            value={phone}
            onChange={(e) => {
              setPhone(e.target.value)
              setPhoneError(null)
            }}
            error={phoneError}
          />
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? 'Sending code…' : 'Send code'}
          </Button>
          <p className="text-sm text-slate-500">
            Signed up with Google? Use Continue with Google instead.
          </p>
        </form>
        <BackToSignIn />
      </AuthShell>
    )
  }

  const masked = maskPhone(phone)
  const last4 = masked.slice(-4)

  return (
    <AuthShell
      title="Reset your password"
      intro={
        <>
          If an account uses <span aria-hidden>{masked}</span>
          <span className="sr-only">the number ending in {last4}</span>, we&apos;ve sent a code.
        </>
      }
    >
      <form noValidate onSubmit={(e) => void handleReset(e)} className="space-y-5">
        {notice && (
          <p role="status" className="text-slate-700">
            {notice}
          </p>
        )}
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <TextField
          label="6-digit code"
          name="code"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]*"
          maxLength={6}
          required
          value={code}
          onChange={(e) => {
            setCode(e.target.value.replace(/\D/g, '').slice(0, 6))
            clearError('code')
          }}
          error={errors.code}
          className="font-mono text-xl tracking-[0.4em]"
        />
        <PasswordField
          label="New password"
          name="new_password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
            clearError('password')
          }}
          hint={PASSWORD_HINT}
          error={errors.password}
        />
        <PasswordField
          label="Confirm new password"
          name="confirm_password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => {
            setConfirm(e.target.value)
            clearError('confirm')
          }}
          error={errors.confirm}
        />
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? 'Resetting password…' : 'Reset password'}
        </Button>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            variant="secondary"
            disabled={busy || resendIn > 0}
            onClick={() => void handleResend()}
          >
            {resendIn > 0 ? `Resend code in ${resendIn}s` : 'Resend code'}
          </Button>
          <Button variant="secondary" onClick={changeNumber} disabled={busy}>
            Use a different number
          </Button>
        </div>
      </form>
      <BackToSignIn />
    </AuthShell>
  )
}

function BackToSignIn() {
  return (
    <p className="text-center text-sm text-slate-600">
      Remembered it? <TextLink to="/login">Sign in</TextLink>
    </p>
  )
}
