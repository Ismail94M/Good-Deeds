import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../../lib/api'

export function HomePage() {
  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-8 px-4 py-12">
      <header className="space-y-3">
        <p className="text-sm font-semibold tracking-wide text-brand-700 uppercase">Good Deeds</p>
        <h1 className="text-4xl font-bold text-balance text-stone-900">
          Make kindness visible. Make goodness contagious.
        </h1>
        <p className="text-lg text-stone-600">
          Recognize the good deeds you see around Bangalore. Celebrate others, and inspire your city
          to do more good.
        </p>
      </header>
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
  let tone = 'bg-stone-300'
  if (isError || data?.status === 'degraded') {
    label = 'Service unavailable'
    tone = 'bg-red-500'
  } else if (!isPending && data) {
    label = 'All systems running'
    tone = 'bg-brand-600'
  }

  return (
    <p role="status" className="flex items-center gap-2 text-sm text-stone-600">
      <span aria-hidden className={`size-2.5 rounded-full ${tone}`} />
      {label}
    </p>
  )
}
