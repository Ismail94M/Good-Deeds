import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HomePage } from './HomePage'

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <HomePage />
    </QueryClientProvider>,
  )
}

function mockFetch(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(JSON.stringify(body), { status }))
}

describe('HomePage', () => {
  it('shows the tagline', () => {
    mockFetch(200, { status: 'ok', database: 'ok', postgis: '3.4' })
    renderPage()
    expect(screen.getByRole('heading', { name: /make kindness visible/i })).toBeInTheDocument()
  })

  it('reports a healthy API', async () => {
    const fetchSpy = mockFetch(200, {
      status: 'ok',
      database: 'ok',
      postgis: '3.4',
    })
    renderPage()
    expect(await screen.findByText('All systems running')).toBeInTheDocument()
    expect(fetchSpy).toHaveBeenCalledWith('/api/health', expect.anything())
  })

  it('reports when the API is down', async () => {
    mockFetch(503, {
      status: 'degraded',
      database: 'unavailable',
      postgis: null,
    })
    renderPage()
    expect(await screen.findByText('Service unavailable')).toBeInTheDocument()
  })
})
