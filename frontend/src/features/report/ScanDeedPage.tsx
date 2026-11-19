import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Loading } from '../../components/ui'
import { useCountdown } from '../../lib/useCountdown'
import { useAuth } from '../auth/authContext'
import {
  deedsApi,
  deedsKeys,
  type DeedOut,
  type QrDeedRequest,
  type ScanDetail,
} from '../deeds/deedsApi'
import { mediaApi, type AttachPhoto } from '../media/mediaApi'
import { useMediaOptions } from '../media/mediaQueries'
import { addUntil } from '../media/mediaText'
import { PhotoPicker } from '../media/PhotoPicker'
import { usePhotoUploads } from '../media/usePhotoUploads'
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
import { ReportResult, type PhotoResult } from './ReportResult'
import { BackButton, ReportMain } from './reportUi'

const FORM_KEYS: ReadonlySet<ErrorKey> = new Set(FORM_ERROR_KEYS)

const PHOTO_HELP =
  'Good deeds are meant to be shared proudly. If someone would rather not be shown, blur their face or remove the photo.'

type SendInput = { request: QrDeedRequest; attach: AttachPhoto[]; skipped: boolean }
type SendOutput = { deed: DeedOut; photos: Omit<PhotoResult, 'addUntil'> | null }

/**
 * Create the deed, then attach the uploaded photos (M3-DESIGN §4.1). A failed attach never
 * fails the send: the deed exists, and the result tells the witness to add photos from My deeds.
 */
async function sendDeed({ request, attach, skipped }: SendInput): Promise<SendOutput> {
  const deed = await deedsApi.createDeed(request)
  if (attach.length === 0) return { deed, photos: skipped ? { attached: 0, failed: true } : null }
  try {
    await mediaApi.attachToDeed(deed.id, { photos: attach })
    return { deed, photos: { attached: attach.length, failed: skipped } }
  } catch {
    return { deed, photos: { attached: 0, failed: true } }
  }
}

/**
 * `/report/scan/:scanId`: the deed form, then the result (M2-DESIGN §5.3). Loads the scan with
 * `GET /api/scans/{id}`, so the form survives a reload; a used scan shows `scan_used`, and the
 * form is replaced by `scan_expired` when its countdown reaches 0. Photos (M3) upload while the
 * form is filled in; Send waits for running uploads, then attaches them after the deed is made.
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
  const [result, setResult] = useState<SendOutput | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [waiting, setWaiting] = useState(false)

  const media = useMediaOptions()
  const mediaOptions = media.data?.photos_enabled ? media.data : undefined
  const uploads = usePhotoUploads({
    purpose: 'deed',
    options: mediaOptions,
    slots: mediaOptions?.photos_per_owner ?? 0,
  })

  const send = useMutation({
    mutationFn: sendDeed,
    onSuccess: (output) => {
      setResult(output)
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
    const windowHours = mediaOptions?.photo_edit_window_hours ?? 24
    return (
      <ReportResult
        deed={result.deed}
        photos={
          result.photos && {
            ...result.photos,
            addUntil: addUntil(result.deed.reported_at, windowHours),
          }
        }
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
    const request = toQrDeedRequest(scanQuery.data, values)
    // Send waits for running uploads, then attaches whatever finished.
    setWaiting(uploads.busy)
    void uploads.settled().then(() => {
      setWaiting(false)
      const { attach, failed } = uploads.snapshot()
      send.mutate({ request, attach, skipped: failed > 0 })
    })
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
        uploadingPhotos={waiting}
        photos={
          mediaOptions && (
            <PhotoPicker uploads={uploads} options={mediaOptions} blur help={PHOTO_HELP} />
          )
        }
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
