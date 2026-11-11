import { useEffect, useState } from 'react'

/**
 * Seconds left until `deadline` (a ms timestamp), ticking once a second. Returns 0 once
 * passed, or when deadline is null.
 */
export function useCountdown(deadline: number | null): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (deadline === null) return
    const tick = () => setNow(Date.now())
    const first = window.setTimeout(tick, 0)
    const timer = window.setInterval(tick, 1000)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [deadline])

  if (deadline === null) return 0
  return Math.max(0, Math.ceil((deadline - now) / 1000))
}
