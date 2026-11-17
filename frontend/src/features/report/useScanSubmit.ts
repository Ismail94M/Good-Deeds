import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'
import { deedsApi, deedsKeys, type ScanDetail } from '../deeds/deedsApi'
import { errorKey } from './errorCopy'
import { LocationNotReady, type LocationGateApi } from './reportContext'

/** Who to scan: the QR token, or the typed ID and code. */
export type ScanIdentity = { token: string } | { short_id: string; code: string }

export type ScanSubmitState =
  | { status: 'idle' }
  | { status: 'checking' }
  /** 404 `doer_code_invalid`: shown inline on the ID + code form. */
  | { status: 'code_mismatch' }
  /** Any other failure, shown full screen with `errorCopy`. */
  | { status: 'error'; error: unknown }

/**
 * Checking → `POST /api/scans` → the deed form (§5.2). The page is replaced in history, so a
 * `/scan/:token` URL leaves it. A fresh position is taken first when the held one is over a
 * minute old; location failures and 400 `location_too_imprecise` go back to the gate's screens.
 */
export function useScanSubmit(gate: LocationGateApi) {
  const [state, setState] = useState<ScanSubmitState>({ status: 'idle' })
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { freshLocation, showImprecise } = gate

  const submit = useCallback(
    async (identity: ScanIdentity) => {
      setState({ status: 'checking' })
      let location
      try {
        location = await freshLocation()
      } catch (error) {
        if (error instanceof LocationNotReady) {
          setState({ status: 'idle' })
          return
        }
        setState({ status: 'error', error })
        return
      }
      try {
        const scan = await deedsApi.createScan({ ...identity, location })
        const detail: ScanDetail = { ...scan, consumed: false }
        queryClient.setQueryData(deedsKeys.scan(scan.scan_id), detail)
        navigate(`/report/scan/${encodeURIComponent(scan.scan_id)}`, { replace: true })
      } catch (error) {
        const key = errorKey(error)
        if (key === 'location_too_imprecise') {
          setState({ status: 'idle' })
          showImprecise(location.accuracy_m)
        } else if (key === 'doer_code_invalid') {
          setState({ status: 'code_mismatch' })
        } else {
          setState({ status: 'error', error })
        }
      }
    },
    [freshLocation, showImprecise, navigate, queryClient],
  )

  const reset = useCallback(() => setState({ status: 'idle' }), [])

  return { state, submit, reset }
}
