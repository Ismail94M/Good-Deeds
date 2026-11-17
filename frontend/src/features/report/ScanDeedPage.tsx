import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Loading } from '../../components/ui'
import { useCountdown } from '../../lib/useCountdown'
import { useAuth } from '../auth/authContext'
import { deedsApi, deedsKeys, type DeedOut, type ScanDetail } from '../deeds/deedsApi'
import { CancelReportDialog } from './CancelReportDialog'
import { DeedForm } from './DeedForm'
import {
  INITIAL_DEED_FORM,
  toQrDeedRequest,
  validateDeedForm,
  type DeedFormValues,
} from './deedFormValues'
import { copyFor, errorCopy, errorKey, FORM_ERROR_KEYS, type ErrorKey } from './errorCopy'
import { firstName } from './format'
import { ReportErrorView } from './ReportErrorView'
import { ReportResult } from './ReportResult'
import { BackButton, ReportMain } from './reportUi'

const FORM_KEYS: ReadonlySet<ErrorKey> = new Set(FORM_ERROR_KEYS)

/**
 * `/report/scan/:scanId`: the deed form, then the result (M2-DESIGN §5.3). Loads the scan with
 * `GET /api/scans/{id}`, so the form survives a reload; a used scan shows `scan_used`, and the
 * form is replaced by `scan_expired` when its countdown reaches 0.
 */
export function ScanDeedPage() {
  const { scanId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const scanQuery = useQuery({
    queryKey: deedsKeys.scan(scanId),
    queryFn: () => deedsApi.scan(scanId),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  })
  const options = useQuery({ queryKey: deedsKeys.options, queryFn: deedsApi.options })

  const [values, setValues] = useState<DeedFormValues>(INITIAL_DEED_FORM)
  const [showErrors, setShowErrors] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [sendError, setSendError] = useState<unknown>(null)
  const [result, setResult] = useState<DeedOut | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)

  const send = useMutation({
    mutationFn: deedsApi.createDeed,
    onSuccess: (deed) => {
      setResult(deed)
      // The scan is used now: Back to this page must show scan_used, not the form (§5.3).
      queryClient.setQueryData<ScanDetail>(deedsKeys.scan(scanId), (scan) =>
        scan ? { ...scan, consumed: true } : scan,
      )
      void queryClient.invalidateQueries({ queryKey: deedsKeys.options })
      void queryClient.invalidateQueries({ queryKey: ['deeds', 'mine'] })
    },
    onError: (error) => {
      if (FORM_KEYS.has(errorKey(error))) setFormError(errorCopy(error).title)
      else setSendError(error)
    },
  })

  const scan = scanQuery.data
  const secondsLeft = useCountdown(scan ? Date.parse(scan.expires_at) : null)

  if (result && scan) {
    return (
      <ReportResult
        deed={result}
        doer={scan.doer}
        reporterName={user?.display_name ?? null}
        categories={options.data?.categories ?? []}
      />
    )
  }
  if (scanQuery.isPending || options.isPending) return <Loading />
  if (scanQuery.isError) {
    return (
      <ReportErrorView
        copy={errorCopy(scanQuery.error, { options: options.data })}
        onRetry={() => void scanQuery.refetch()}
      />
    )
  }
  if (options.isError) {
    return <ReportErrorView copy={copyFor('generic')} onRetry={() => void options.refetch()} />
  }

  const context = {
    doer: scanQuery.data.doer,
    options: options.data,
    monthResetsAt: scanQuery.data.month_resets_at,
  }
  if (sendError !== null) {
    return (
      <ReportErrorView copy={errorCopy(sendError, context)} onRetry={() => setSendError(null)} />
    )
  }
  if (scanQuery.data.consumed) return <ReportErrorView copy={copyFor('scan_used', context)} />
  if (secondsLeft <= 0) return <ReportErrorView copy={copyFor('scan_expired', context)} />

  const onSubmit = () => {
    setFormError(null)
    const errors = validateDeedForm(values, options.data)
    if (errors.category || errors.description) {
      setShowErrors(true)
      return
    }
    send.mutate(toQrDeedRequest(scanQuery.data, values))
  }

  return (
    <ReportMain>
      <BackButton label="Cancel" onClick={() => setConfirmCancel(true)} />
      <DeedForm
        scan={scanQuery.data}
        options={options.data}
        secondsLeft={secondsLeft}
        values={values}
        onChange={(patch) => setValues((current) => ({ ...current, ...patch }))}
        showErrors={showErrors}
        formError={formError}
        sending={send.isPending}
        onSubmit={onSubmit}
      />
      {confirmCancel && (
        <CancelReportDialog
          doerFirstName={firstName(scanQuery.data.doer.display_name)}
          onKeep={() => setConfirmCancel(false)}
          onCancelReport={() => navigate('/report')}
        />
      )}
    </ReportMain>
  )
}
