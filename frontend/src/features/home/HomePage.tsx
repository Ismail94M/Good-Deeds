import { useQuery } from '@tanstack/react-query'
import { LinkButton, Loading } from '../../components/ui'
import { getHealth } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { FeedPage } from '../feed/FeedPage'

/** `/`: the feed for signed-in users (M3-DESIGN §6.2), the welcome page for everyone else. */
export function HomePage() {
  const { user, isLoading } = useAuth()
  if (isLoading) return <Loading />
  if (user) return <FeedPage />
  return <WelcomePage />
}

function WelcomePage() {
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

      <div className="grid grid-cols-2 gap-3">
        <LinkButton to="/signup">Sign up</LinkButton>
        <LinkButton to="/login" variant="secondary">
          Sign in
        </LinkButton>
      </div>

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
