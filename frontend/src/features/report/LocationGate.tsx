import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { Loading } from '../../components/ui'
import {
  GeolocationError,
  getPosition,
  permissionState,
  type Location,
} from '../../lib/geolocation'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'
import { copyFor } from './errorCopy'
import {
  LOCATION_MAX_AGE_MS,
  LocationNotReady,
  useReportContext,
  type LocationGateApi,
} from './reportContext'
import { ReportErrorView } from './ReportErrorView'
import {
  BackButton,
  Banner,
  CheckIcon,
  NumberedSteps,
  primaryButton,
  ReportMain,
  ScreenTitle,
  WarningIcon,
  AlertIcon,
} from './reportUi'

type Phase =
  | { kind: 'init' }
  | { kind: 'explain' }
  | { kind: 'locating' }
  | { kind: 'denied' }
  | { kind: 'imprecise'; accuracyM: number }
  | { kind: 'unavailable' }
  | { kind: 'ready' }

/**
 * Location before the camera, the ID + code form or a `/scan/:token` link (M2-DESIGN §5.3).
 * Explains first unless permission is already granted, then shows the denied, not precise
 * enough or couldn't-find screens, each with Try again. Once a position is held, renders
 * `children` with the gate's API.
 */
export function LocationGate({ children }: { children: (gate: LocationGateApi) => ReactNode }) {
  const options = useQuery({ queryKey: deedsKeys.options, queryFn: deedsApi.options })
  const { held, setHeld } = useReportContext()
  const [phase, setPhase] = useState<Phase>(() => (held ? { kind: 'ready' } : { kind: 'init' }))
  const navigate = useNavigate()
  const maxAccuracy = options.data?.max_location_accuracy_m ?? null

  const acquire = useCallback(
    async (max: number): Promise<Location> => {
      let location: Location
      try {
        location = await getPosition()
      } catch (error) {
        setHeld(null)
        setPhase(
          error instanceof GeolocationError && error.kind === 'denied'
            ? { kind: 'denied' }
            : { kind: 'unavailable' },
        )
        throw new LocationNotReady()
      }
      if (location.accuracy_m > max) {
        setHeld(null)
        setPhase({ kind: 'imprecise', accuracyM: location.accuracy_m })
        throw new LocationNotReady()
      }
      setHeld({ location, at: Date.now() })
      setPhase({ kind: 'ready' })
      return location
    },
    [setHeld],
  )

  const locate = useCallback(() => {
    if (maxAccuracy === null) return
    setPhase({ kind: 'locating' })
    acquire(maxAccuracy).catch(() => {
      // The phase already shows what went wrong.
    })
  }, [acquire, maxAccuracy])

  // First visit: skip the explain screen when the browser already allows location.
  useEffect(() => {
    if (phase.kind !== 'init' || maxAccuracy === null) return
    let cancelled = false
    void permissionState().then((state) => {
      if (cancelled) return
      if (state === 'granted') locate()
      else if (state === 'denied') setPhase({ kind: 'denied' })
      else setPhase({ kind: 'explain' })
    })
    return () => {
      cancelled = true
    }
  }, [phase.kind, maxAccuracy, locate])

  const freshLocation = useCallback(async (): Promise<Location> => {
    if (held && Date.now() - held.at < LOCATION_MAX_AGE_MS) return held.location
    if (maxAccuracy === null) throw new LocationNotReady()
    return acquire(maxAccuracy)
  }, [held, maxAccuracy, acquire])

  const showImprecise = useCallback(
    (accuracyM: number) => {
      setHeld(null)
      setPhase({ kind: 'imprecise', accuracyM })
    },
    [setHeld],
  )

  if (options.isPending) return <Loading />
  if (options.isError) {
    return <ReportErrorView copy={copyFor('generic')} onRetry={() => void options.refetch()} />
  }

  const back = () => navigate('/report')

  if (phase.kind === 'ready' && held) {
    return children({ location: held.location, freshLocation, showImprecise })
  }
  if (phase.kind === 'explain') {
    return (
      <ReportMain>
        <BackButton onClick={back} />
        <div className="flex flex-col gap-4.5">
          <span
            aria-hidden
            className="flex size-16 items-center justify-center rounded-[18px] bg-brand-50 text-brand-700"
          >
            <svg
              viewBox="0 0 24 24"
              className="size-8"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z" />
              <circle cx="12" cy="9.5" r="2.5" />
            </svg>
          </span>
          <ScreenTitle>Allow your location</ScreenTitle>
          <p className="text-slate-700">
            We check that you and the person you’re recognising are in the same place, so deeds
            can’t be faked from far away.
          </p>
          <ul className="flex flex-col gap-3">
            {[
              'Your exact location is never shown to anyone.',
              'It’s only used to check this report.',
              'Good Deeds works in Bangalore for now.',
            ].map((point) => (
              <li key={point} className="flex items-start gap-2.5">
                <span className="mt-px text-emerald-700">
                  <CheckIcon />
                </span>
                <span>{point}</span>
              </li>
            ))}
          </ul>
          <button type="button" onClick={locate} className={primaryButton}>
            Allow location
          </button>
          <p className="text-center text-sm text-[#586579]">
            Your phone will ask next. Choose <strong>Allow</strong>.
          </p>
        </div>
      </ReportMain>
    )
  }
  if (phase.kind === 'denied') {
    const copy = copyFor('location_denied')
    return (
      <ReportMain>
        <BackButton onClick={back} />
        <div className="flex flex-col gap-4.5">
          <ScreenTitle>{copy.title}</ScreenTitle>
          <Banner tone="danger" role="alert" icon={<AlertIcon />}>
            {copy.body}
          </Banner>
          <h2 className="text-[17px] font-bold text-slate-900">How to turn it on</h2>
          <NumberedSteps
            steps={[
              'Tap the icon to the left of the web address (a lock or settings icon).',
              <>
                Find <strong>Location</strong> and choose <strong>Allow</strong>.
              </>,
              <>
                Come back here and tap <strong>Try again</strong>.
              </>,
            ]}
          />
          <p className="text-sm text-[#586579]">
            Still not working? Check that Location is switched on in your phone’s settings.
          </p>
          <button type="button" onClick={locate} className={primaryButton}>
            Try again
          </button>
        </div>
      </ReportMain>
    )
  }
  if (phase.kind === 'imprecise' || phase.kind === 'unavailable') {
    const max = options.data.max_location_accuracy_m
    const title =
      phase.kind === 'imprecise'
        ? copyFor('location_too_imprecise').title
        : copyFor('location_unavailable').title
    return (
      <ReportMain>
        <BackButton onClick={back} />
        <div className="flex flex-col gap-4.5">
          <ScreenTitle>{title}</ScreenTitle>
          <Banner tone="warning" role="alert" icon={<WarningIcon />}>
            {phase.kind === 'imprecise' ? (
              <>
                Your phone places you somewhere within <strong>{phase.accuracyM} m</strong>. We need
                it within {max} m.
              </>
            ) : (
              copyFor('location_unavailable').body
            )}
          </Banner>
          <h2 className="text-[17px] font-bold text-slate-900">Try this</h2>
          <ul className="flex list-disc flex-col gap-2 pl-5">
            <li>
              Turn on <strong>precise location</strong> for your browser.
            </li>
            <li>Step outside, or move near a window.</li>
            <li>Wait a few seconds, then try again.</li>
          </ul>
          <button type="button" onClick={locate} className={primaryButton}>
            Try again
          </button>
        </div>
      </ReportMain>
    )
  }
  return <Loading label="Finding your location…" />
}
