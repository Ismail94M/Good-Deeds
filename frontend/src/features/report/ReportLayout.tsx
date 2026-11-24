import { useMemo, useState } from 'react'
import { Outlet } from 'react-router'
import { ReportContext, type HeldLocation } from './reportContext'

/**
 * Wraps the Record a deed routes so they share the held position and a scanned link's token
 * (see `reportContext`).
 */
export function ReportLayout() {
  const [held, setHeld] = useState<HeldLocation | null>(null)
  const [pendingToken, setPendingToken] = useState<string | null>(null)
  const value = useMemo(
    () => ({ held, setHeld, pendingToken, setPendingToken }),
    [held, pendingToken],
  )
  return (
    <ReportContext.Provider value={value}>
      <Outlet />
    </ReportContext.Provider>
  )
}
