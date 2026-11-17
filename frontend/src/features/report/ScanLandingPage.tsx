import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'
import { CheckingView } from './CheckingView'
import { copyFor, errorCopy } from './errorCopy'
import { LocationGate } from './LocationGate'
import { useReportContext, type LocationGateApi } from './reportContext'
import { ReportErrorView } from './ReportErrorView'
import { useScanSubmit } from './useScanSubmit'

/** Where a `/scan/:token` link continues once the token is out of the URL. */
export const SCAN_LINK_PATH = '/report/link'

/**
 * `/scan/:token`, where a phone's own camera app lands. The token is read into memory and the
 * URL is replaced at once with `/report/link`, so `/scan/<token>` never stays in the history,
 * whatever happens next (security review R5-L2).
 */
export function ScanLandingPage() {
  const { token = '' } = useParams()
  const { setPendingToken } = useReportContext()
  const navigate = useNavigate()
  useEffect(() => {
    setPendingToken(token || null)
    navigate(SCAN_LINK_PATH, { replace: true })
  }, [token, setPendingToken, navigate])
  return <CheckingView />
}

/**
 * `/report/link`: the location gate, then Checking and `POST /api/scans` with the token held in
 * memory. On success the page is replaced by the deed form (§5.2). Opened without a token
 * (e.g. after a reload, which clears memory) it asks for a new code, like `qr_invalid`.
 */
export function ScanLinkPage() {
  const { pendingToken, setPendingToken } = useReportContext()
  const navigate = useNavigate()
  // Take the token once; the context copy is cleared so it isn't reused by another visit.
  const [token] = useState(pendingToken)
  useEffect(() => {
    if (pendingToken) setPendingToken(null)
  }, [pendingToken, setPendingToken])

  if (!token) {
    return (
      <ReportErrorView
        copy={copyFor('qr_invalid')}
        onScanAgain={() => navigate('/report/scan', { replace: true })}
      />
    )
  }
  return <LocationGate>{(gate) => <LandingCheck gate={gate} token={token} />}</LocationGate>
}

function LandingCheck({ gate, token }: { gate: LocationGateApi; token: string }) {
  const navigate = useNavigate()
  const options = useQuery({ queryKey: deedsKeys.options, queryFn: deedsApi.options })
  const { state, submit } = useScanSubmit(gate)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    void submit({ token })
  }, [submit, token])

  if (state.status === 'error' || state.status === 'code_mismatch') {
    const retry = () => void submit({ token })
    return (
      <ReportErrorView
        copy={errorCopy(state.status === 'error' ? state.error : null, {
          options: options.data,
          accuracyM: gate.location.accuracy_m,
        })}
        onRetry={retry}
        // The link's code is used up or stale; scanning again happens in the app.
        onScanAgain={() => navigate('/report/scan', { replace: true })}
      />
    )
  }
  return <CheckingView />
}
