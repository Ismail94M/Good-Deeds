import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { Button, ErrorAlert, Page, TextField, TextLink } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from './authContext'
import { ConsentFields } from './ConsentFields'
import { SocialButtons } from './social/SocialButtons'
import {
  collectErrors,
  consentErrors,
  validateDisplayName,
  validateEmail,
  validateHandle,
  validatePassword,
  type ConsentValues,
} from './validation'

type Values = ConsentValues & {
  display_name: string
  handle: string
  email: string
  password: string
}
type Field = keyof Values

const EMPTY: Values = {
  display_name: '',
  handle: '',
  email: '',
  password: '',
  date_of_birth: '',
  accept_terms: false,
  accept_privacy: false,
}

/** Which form field an API error code belongs to. */
const CODE_FIELDS: Record<string, Field> = {
  email_taken: 'email',
  handle_taken: 'handle',
  underage: 'date_of_birth',
}

export function SignupPage() {
  const { signup } = useAuth()
  const [params] = useSearchParams()
  const [values, setValues] = useState<Values>(EMPTY)
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function update(patch: Partial<Values>) {
    setValues((v) => ({ ...v, ...patch }))
    setErrors((e) => {
      const next = { ...e }
      for (const key of Object.keys(patch) as Field[]) delete next[key]
      return next
    })
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    const found: Partial<Record<Field, string>> = {
      ...collectErrors({
        display_name: validateDisplayName(values.display_name),
        handle: validateHandle(values.handle),
        email: validateEmail(values.email),
        password: validatePassword(values.password),
      }),
      ...consentErrors(values),
    }
    setErrors(found)
    if (Object.keys(found).length > 0) {
      setFormError('Please fix the highlighted details.')
      return
    }

    setSubmitting(true)
    try {
      // RedirectIfAuthed sends the user on to onboarding once they're signed in.
      await signup({
        display_name: values.display_name.trim(),
        handle: values.handle.trim(),
        email: values.email.trim(),
        password: values.password,
        date_of_birth: values.date_of_birth,
        accept_terms: true,
        accept_privacy: true,
      })
    } catch (err) {
      if (err instanceof ApiError) {
        const field = err.code ? CODE_FIELDS[err.code] : undefined
        if (field) setErrors({ [field]: err.detail })
        else if (Object.keys(err.fieldErrors).length) setErrors(err.fieldErrors)
      }
      setFormError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  const query = params.size ? `?${params.toString()}` : ''

  return (
    <Page title="Join Good Deeds" intro="Celebrate the kindness you see around Bangalore.">
      <form noValidate onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
        {formError && <ErrorAlert>{formError}</ErrorAlert>}
        <TextField
          label="Your name"
          name="display_name"
          autoComplete="name"
          required
          value={values.display_name}
          onChange={(e) => update({ display_name: e.target.value })}
          error={errors.display_name}
        />
        <TextField
          label="Handle"
          name="handle"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          required
          value={values.handle}
          onChange={(e) => update({ handle: e.target.value })}
          hint="3–30 letters, numbers, _ or . This is your public @name."
          error={errors.handle}
        />
        <TextField
          label="Email"
          name="email"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={values.email}
          onChange={(e) => update({ email: e.target.value })}
          error={errors.email}
        />
        <TextField
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          value={values.password}
          onChange={(e) => update({ password: e.target.value })}
          hint="At least 10 characters."
          error={errors.password}
        />
        <ConsentFields values={values} errors={errors} onChange={update} />
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? 'Creating your account…' : 'Sign up'}
        </Button>
      </form>
      <SocialButtons />
      <p className="text-center text-stone-600">
        Already have an account? <TextLink to={`/login${query}`}>Log in</TextLink>
      </p>
    </Page>
  )
}
