import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { AuthProvider } from '../features/auth/AuthProvider'
import { ApiError } from '../lib/api'
import { routes } from './routes'

const router = createBrowserRouter(routes)

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Don't retry client errors (401/403/404…); retry network blips once.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
      },
    },
  })
}

export function App() {
  const [queryClient] = useState(makeQueryClient)
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  )
}
