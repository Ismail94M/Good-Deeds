import { useState } from 'react'
import { Checkbox, TextField, TextLink } from '../../components/ui'
import {
  ageOn,
  MIN_AGE,
  UNDERAGE_MESSAGE,
  type ConsentErrors,
  type ConsentValues,
} from './validation'

/** Date of birth plus the two required consent checkboxes (used at sign-up and onboarding). */
export function ConsentFields({
  values,
  errors,
  onChange,
}: {
  values: ConsentValues
  errors: ConsentErrors
  onChange: (patch: Partial<ConsentValues>) => void
}) {
  const age = ageOn(values.date_of_birth)
  const underage = age !== null && age >= 0 && age < MIN_AGE
  const [today] = useState(() => new Date().toISOString().slice(0, 10))

  return (
    <>
      <TextField
        label="Date of birth"
        name="date_of_birth"
        type="date"
        max={today}
        autoComplete="bday"
        required
        value={values.date_of_birth}
        onChange={(e) => onChange({ date_of_birth: e.target.value })}
        hint="Only used to check you're 18 or older. We don't store it."
        error={underage ? null : errors.date_of_birth}
      />
      {underage && (
        <p role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
          {UNDERAGE_MESSAGE}
        </p>
      )}
      <fieldset className="space-y-3">
        <legend className="sr-only">Agreements</legend>
        <Checkbox
          name="accept_terms"
          checked={values.accept_terms}
          onChange={(e) => onChange({ accept_terms: e.target.checked })}
          error={errors.accept_terms}
          label={
            <>
              I agree to the{' '}
              <TextLink to="/terms" target="_blank">
                Terms
              </TextLink>
            </>
          }
        />
        <Checkbox
          name="accept_privacy"
          checked={values.accept_privacy}
          onChange={(e) => onChange({ accept_privacy: e.target.checked })}
          error={errors.accept_privacy}
          label={
            <>
              I have read the{' '}
              <TextLink to="/privacy" target="_blank">
                Privacy Notice
              </TextLink>
            </>
          }
        />
      </fieldset>
    </>
  )
}
