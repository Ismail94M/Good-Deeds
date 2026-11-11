import { useQuery } from '@tanstack/react-query'
import { LinkButton, TextLink } from '../../components/ui'
import { getHealth } from '../../lib/api'
import { useAuth } from '../auth/authContext'

export function HomePage() {
  const { user, isLoading } = useAuth()

  return (
    <main className="mx-auto flex w-full max-w-xl flex-col gap-8 px-4 py-10 sm:py-16">
      <header className="space-y-3">
        <h1 className="text-4xl font-bold text-balance text-slate-900">
          Make kindness visible. Make goodness contagious.
        </h1>
        <p className="text-lg text-slate-600">
          Recognize the good deeds you see around Bangalore. Celebrate others, and inspire your city
          to do more good.
        </p>
      </header>

      {isLoading ? null : user ? (
        <section className="space-y-4" aria-label="Your shortcuts">
          <p className="text-xl font-semibold">Welcome, {user.display_name}!</p>
          {!user.onboarding.complete && (
            <p className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
              You&apos;re nearly there. <TextLink to="/onboarding">Finish setting up</TextLink> to
              get your QR code.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3">
            <LinkButton to="/me/qr">My QR</LinkButton>
            <LinkButton to="/profile" variant="secondary">
              Profile
            </LinkButton>
          </div>
        </section>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <LinkButton to="/signup">Sign up</LinkButton>
          <LinkButton to="/login" variant="secondary">
            Sign in
          </LinkButton>
        </div>
      )}

      <ApiStatus />
    </main>
  )
}

function ApiStatus() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    retry: false,
  })

  let label = 'Checking connection…'
  let tone = 'bg-slate-300'
  if (isError || data?.status === 'degraded') {
    label = 'Service unavailable'
    tone = 'bg-red-500'
  } else if (!isPending && data) {
    label = 'All systems running'
    tone = 'bg-brand-600'
  }

  return (
    <p role="status" className="mt-auto flex items-center gap-2 text-xs text-slate-500">
      <span aria-hidden className={`size-2 rounded-full ${tone}`} />
      {label}
    </p>
  )
}
