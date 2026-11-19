import { useEffect, useState, type ReactNode } from 'react'

/** A short message for the last action ("Link copied"); hides itself after a few seconds. */
export function useToast(): [ReactNode, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(() => setMessage(null), 3000)
    return () => window.clearTimeout(timer)
  }, [message, nonce])
  const node = (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-4"
    >
      {message && (
        <p className="rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow-lg">
          {message}
        </p>
      )}
    </div>
  )
  return [
    node,
    (next: string) => {
      setMessage(next)
      setNonce((n) => n + 1)
    },
  ]
}
