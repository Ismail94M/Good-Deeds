import { useState, type FormEvent } from 'react'
import { Button, ErrorAlert, TextField } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { useCountdown } from '../../lib/useCountdown'
import { useAuth } from '../auth/authContext'
import { identityApi } from '../auth/identityApi'

type Sent = { phone: string; resendAt: number }

export function PhoneStep() {
  const { setMe } = useAuth()
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [sent, setSent] = useState<Sent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const resendIn = useCountdown(sent?.resendAt ?? null)

  async function sendCode(raw: string) {
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      const result = await identityApi.phoneStart(raw)
      setSent({
        phone: result.phone_e164,
        resendAt: Date.now() + result.resend_after_seconds * 1000,
      })
      setCode('')
      setNotice(`Enter the 6-digit code we sent to ${result.phone_e164}.`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  function handleStart(event: FormEvent) {
    event.preventDefault()
    const digits = phone.replace(/\D/g, '')
    if (digits.length < 8) {
      setError('Please enter your Indian mobile number, for example 98765 43210.')
      return
    }
    void sendCode(phone.trim())
  }

  async function handleVerify(event: FormEvent) {
    event.preventDefault()
    if (!sent) return
    if (!/^\d{6}$/.test(code)) {
      setError('Please enter the 6-digit code from the SMS.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      setMe(await identityApi.phoneVerify(sent.phone, code))
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  function changeNumber() {
    setSent(null)
    setCode('')
    setError(null)
    setNotice(null)
  }

  if (!sent) {
    return (
      <form noValidate onSubmit={handleStart} className="space-y-5">
        <h2 className="text-lg font-semibold">Verify your phone</h2>
        <p className="text-slate-600">
          This keeps Good Deeds free of fake accounts. We won&apos;t show your number to anyone.
        </p>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <TextField
          label="Indian mobile number"
          name="phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          placeholder="+91 98765 43210"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          hint="10 digits. You can leave out +91. Only Indian numbers work for now."
        />
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? 'Sending code…' : 'Send code'}
        </Button>
      </form>
    )
  }

  return (
    <form noValidate onSubmit={(e) => void handleVerify(e)} className="space-y-5">
      <h2 className="text-lg font-semibold">Enter your code</h2>
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
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
        className="font-mono text-xl tracking-[0.4em]"
      />
      <Button type="submit" disabled={busy} className="w-full">
        {busy ? 'Checking…' : 'Verify'}
      </Button>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="secondary"
          disabled={busy || resendIn > 0}
          onClick={() => void sendCode(sent.phone)}
        >
          {resendIn > 0 ? `Resend code in ${resendIn}s` : 'Resend code'}
        </Button>
        <Button variant="secondary" onClick={changeNumber} disabled={busy}>
          Change number
        </Button>
      </div>
    </form>
  )
}
