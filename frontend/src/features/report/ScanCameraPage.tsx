import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'
import { canScanInApp, isDesktopPointer } from './camera'
import { CheckingView } from './CheckingView'
import { errorCopy } from './errorCopy'
import { LocationGate } from './LocationGate'
import { parseScanUrl } from './parseScanUrl'
import { QrCamera } from './QrCamera'
import type { LocationGateApi } from './reportContext'
import { ReportErrorView } from './ReportErrorView'
import {
  BackButton,
  Banner,
  focusRing,
  LocationChip,
  NumberedSteps,
  ReportMain,
  ScreenTitle,
  secondaryButton,
} from './reportUi'
import { useScanSubmit } from './useScanSubmit'

/**
 * `/report/scan`: the in-app camera behind the location gate, or the "use your camera app"
 * screen when the browser has no `BarcodeDetector`, no camera, or the camera was refused.
 */
export function ScanCameraPage() {
  const [cameraFailed, setCameraFailed] = useState(false)
  if (cameraFailed || !canScanInApp()) return <CameraAppScreen />
  return (
    <LocationGate>
      {(gate) => <CameraStage gate={gate} onCameraFailed={() => setCameraFailed(true)} />}
    </LocationGate>
  )
}

function CameraStage({
  gate,
  onCameraFailed,
}: {
  gate: LocationGateApi
  onCameraFailed: () => void
}) {
  const navigate = useNavigate()
  const options = useQuery({ queryKey: deedsKeys.options, queryFn: deedsApi.options })
  const { state, submit, reset } = useScanSubmit(gate)
  const [foreign, setForeign] = useState(false)
  const submitted = useRef(false)

  if (state.status === 'checking') return <CheckingView />
  if (state.status === 'error' || state.status === 'code_mismatch') {
    const error = state.status === 'error' ? state.error : null
    const again = () => {
      submitted.current = false
      reset()
    }
    return (
      <ReportErrorView
        copy={errorCopy(error, {
          options: options.data,
          accuracyM: gate.location.accuracy_m,
        })}
        onRetry={again}
        onScanAgain={again}
      />
    )
  }

  const onCode = (raw: string) => {
    if (submitted.current) return
    const token = parseScanUrl(raw)
    if (!token) {
      setForeign(true)
      return
    }
    submitted.current = true
    void submit({ token })
  }

  return (
    <main className="fixed inset-0 z-30 flex flex-col overflow-y-auto bg-[#0b1220] text-white">
      <div className="flex items-center justify-between gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => navigate('/report')}
          className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/30 px-3.5 text-[15px] font-semibold text-white ${focusRing} focus-visible:outline-white`}
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
          Close
        </button>
        <LocationChip accuracyM={gate.location.accuracy_m} dark />
      </div>
      <div className="flex grow flex-col items-center justify-center gap-6 px-4 py-6">
        <h1 className="text-center text-[22px] font-bold">Scan their QR</h1>
        <div className="relative flex size-[290px] max-w-full items-center justify-center rounded-3xl bg-slate-800">
          <QrCamera onCode={onCode} onUnavailable={onCameraFailed} />
          <Corner className="top-4.5 left-4.5 rounded-tl-[14px] border-t-4 border-l-4" />
          <Corner className="top-4.5 right-4.5 rounded-tr-[14px] border-t-4 border-r-4" />
          <Corner className="bottom-4.5 left-4.5 rounded-bl-[14px] border-b-4 border-l-4" />
          <Corner className="right-4.5 bottom-4.5 rounded-br-[14px] border-r-4 border-b-4" />
        </div>
        <p className="max-w-[300px] text-center text-slate-200">
          Point your camera at the QR on their phone. It scans by itself.
        </p>
        <p role="status" className="min-h-6 text-center text-sm font-semibold text-amber-200">
          {foreign ? 'That’s not a Good Deeds QR' : ''}
        </p>
      </div>
      <div className="px-4 pt-4 pb-8">
        <button
          type="button"
          onClick={() => navigate('/report/code')}
          className={`min-h-12 w-full rounded-lg border border-white/40 text-base font-semibold text-white ${focusRing} focus-visible:outline-white`}
        >
          Enter ID and code instead
        </button>
      </div>
    </main>
  )
}

function Corner({ className }: { className: string }) {
  return (
    <span
      aria-hidden
      className={`pointer-events-none absolute size-11 border-white ${className}`}
    />
  )
}

/** "Scan with your camera app": no in-app scanning in this browser (F3 canvas). */
export function CameraAppScreen() {
  const navigate = useNavigate()
  return (
    <ReportMain>
      <BackButton onClick={() => navigate('/report')} />
      <div className="flex flex-col gap-4.5">
        <ScreenTitle>Scan with your camera app</ScreenTitle>
        <p className="text-slate-700">
          This browser can’t scan inside Good Deeds. Use your phone’s own camera instead. It opens
          Good Deeds and carries on from here.
        </p>
        <NumberedSteps
          steps={[
            <>
              Open your phone’s <strong>Camera</strong> app.
            </>,
            'Point it at the QR on their phone.',
            'Tap the Good Deeds link that appears.',
          ]}
        />
        {isDesktopPointer() && (
          <Banner tone="info">
            On a computer? Scanning works best on your phone. Or use their ID and code below.
          </Banner>
        )}
        <div className="flex items-center gap-3 text-sm text-[#586579]">
          <span aria-hidden className="h-px grow bg-slate-200" />
          or
          <span aria-hidden className="h-px grow bg-slate-200" />
        </div>
        <button type="button" onClick={() => navigate('/report/code')} className={secondaryButton}>
          Enter ID and code instead
        </button>
      </div>
    </ReportMain>
  )
}
