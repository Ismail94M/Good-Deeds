import { useState, type FormEvent } from 'react'
import { Button, ErrorAlert } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { ConsentFields } from '../auth/ConsentFields'
import { identityApi } from '../auth/identityApi'
import { consentErrors, type ConsentErrors, type ConsentValues } from '../auth/validation'

export function AgeConsentStep() {
  const { setMe } = useAuth()
  const [values, setValues] = useState<ConsentValues>({
    date_of_birth: '',
    accept_terms: false,
    accept_privacy: false,
  })
  const [errors, setErrors] = useState<ConsentErrors>({})
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const found = consentErrors(values)
    setErrors(found)
    if (Object.keys(found).length) return
    setError(null)
    setSubmitting(true)
    try {
      setMe(
        await identityApi.ageConsent({
          date_of_birth: values.date_of_birth,
          accept_terms: true,
          accept_privacy: true,
        }),
      )
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form noValidate onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
      <h2 className="text-lg font-semibold">Confirm your age</h2>
      <p className="text-stone-600">Good Deeds is for people aged 18 and over.</p>
      {error && <ErrorAlert>{error}</ErrorAlert>}
      <ConsentFields
        values={values}
        errors={errors}
        onChange={(patch) => {
          setValues((v) => ({ ...v, ...patch }))
          setErrors({})
        }}
      />
      <Button type="submit" disabled={submitting} className="w-full">
        {submitting ? 'Saving…' : 'Continue'}
      </Button>
    </form>
  )
}
