import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { Button, ErrorAlert, PasswordField, TextField, TextLink } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { AuthShell } from './AuthShell'
import { useAuth } from './authContext'
import { SocialButtons } from './social/SocialButtons'

export function LoginPage() {
  const { login } = useAuth()
  const [params] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!email.trim() || !password) {
      setError('Please enter your email and password.')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      // RedirectIfAuthed sends the user on once they're signed in.
      await login(email.trim(), password)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  const query = params.size ? `?${params.toString()}` : ''

  return (
    <AuthShell
      title="Small acts. A world of good."
      intro="Celebrate the kindness you see around you, and inspire a community that values every small act."
    >
      <form noValidate onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <TextField
          label="Email address"
          placeholder="you@example.com"
          name="email"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <PasswordField
          label="Password"
          placeholder="Enter your password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? 'Signing in…' : 'Sign in'}
          {!submitting && (
            <span aria-hidden className="ml-2">
              →
            </span>
          )}
        </Button>
      </form>
      <SocialButtons />
      <p className="text-center text-sm text-slate-600">
        New here? <TextLink to={`/signup${query}`}>Create an account</TextLink>
      </p>
    </AuthShell>
  )
}
