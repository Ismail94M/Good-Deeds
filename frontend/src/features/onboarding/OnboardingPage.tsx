import { Navigate, useSearchParams } from 'react-router'
import { Loading, Page } from '../../components/ui'
import { useAuth } from '../auth/authContext'
import { safeNext } from '../auth/validation'
import { AgeConsentStep } from './AgeConsentStep'
import { PhoneStep } from './PhoneStep'

type Step = { key: 'age' | 'phone'; label: string; done: boolean }

/** Walks a signed-in user through whatever setup is still missing. */
export function OnboardingPage() {
  const { user } = useAuth()
  const [params] = useSearchParams()
  if (!user) return <Loading />

  const { adult, consents, phone, complete } = user.onboarding
  if (complete) return <Navigate to={safeNext(params.get('next'), '/me/qr')} replace />

  const steps: Step[] = [
    { key: 'age', label: 'Age and consent', done: adult && consents },
    { key: 'phone', label: 'Verify your phone', done: phone },
  ]
  const currentIndex = steps.findIndex((s) => !s.done)
  const current = steps[currentIndex]

  return (
    <Page
      title="Finish setting up"
      intro="A couple of quick steps so people know your good deeds are really yours."
    >
      <ol className="grid grid-cols-2 gap-2" aria-label="Setup steps">
        {steps.map((step, i) => {
          const isCurrent = i === currentIndex
          return (
            <li
              key={step.key}
              aria-current={isCurrent ? 'step' : undefined}
              className={`rounded-lg border px-3 py-2 text-sm ${
                isCurrent
                  ? 'border-brand-700 bg-brand-50 font-semibold text-brand-900'
                  : 'border-stone-200 text-stone-600'
              }`}
            >
              <span className="block text-xs tracking-wide uppercase">
                Step {i + 1} {step.done && <span>· Done ✓</span>}
              </span>
              {step.label}
            </li>
          )
        })}
      </ol>
      <section className="rounded-2xl border border-stone-200 bg-white p-4 sm:p-6">
        {current.key === 'age' ? <AgeConsentStep /> : <PhoneStep />}
      </section>
    </Page>
  )
}
