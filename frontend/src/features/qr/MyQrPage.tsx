import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button, ErrorAlert, LinkButton, Loading, Page } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useCountdown } from '../../lib/useCountdown'
import { identityApi, queryKeys, type QrCode } from '../auth/identityApi'
import { QrImage } from './QrImage'

const refreshMs = (qr: QrCode | undefined) =>
  qr ? Math.max(1, qr.refresh_after_seconds) * 1000 : false

export function MyQrPage() {
  const queryClient = useQueryClient()
  const { data, error, isPending, dataUpdatedAt } = useQuery({
    queryKey: queryKeys.qr,
    queryFn: identityApi.qr,
    // The token changes every window: refetch exactly when the server says to.
    refetchInterval: (query) => refreshMs(query.state.data),
    staleTime: (query) => refreshMs(query.state.data) || 0,
    retry: false,
  })
  const [confirming, setConfirming] = useState(false)
  const [resetDone, setResetDone] = useState(false)

  const rotate = useMutation({
    mutationFn: identityApi.rotateQr,
    onSuccess: (qr) => {
      queryClient.setQueryData(queryKeys.qr, qr)
      setConfirming(false)
      setResetDone(true)
    },
  })

  const nextRefresh = data ? dataUpdatedAt + data.refresh_after_seconds * 1000 : null
  const secondsLeft = useCountdown(nextRefresh)

  if (isPending) return <Loading label="Loading your QR code…" />

  if (error) {
    const needsSetup = error instanceof ApiError && error.code === 'onboarding_required'
    return (
      <Page title="My QR code">
        <ErrorAlert>{errorMessage(error)}</ErrorAlert>
        {needsSetup && <LinkButton to="/onboarding">Finish setting up</LinkButton>}
      </Page>
    )
  }

  return (
    <Page
      title="My QR code"
      intro="Show this to someone who saw your good deed. They scan it to recognise you."
    >
      <QrImage value={data.scan_url} label={`QR code for ${data.display_id}`} />
      <p className="text-center text-sm text-stone-500" aria-live="off">
        {secondsLeft > 0 ? `Refreshes in ${secondsLeft}s` : 'Refreshing…'} · it changes often so
        screenshots can&apos;t be reused
      </p>

      <section className="space-y-1 text-center">
        <h2 className="text-sm font-semibold tracking-wide text-stone-600 uppercase">
          Or share your ID
        </h2>
        <p className="font-mono text-3xl font-bold tracking-wider text-stone-900 select-all sm:text-4xl">
          {data.display_id}
        </p>
        <p className="text-sm text-stone-500">They can type this into the app instead.</p>
      </section>

      <section className="space-y-3 rounded-2xl border border-stone-200 bg-white p-4">
        <h2 className="font-semibold">Worried someone copied your QR?</h2>
        {resetDone && (
          <p role="status" className="text-brand-700">
            Your QR code has been reset. Old screenshots no longer work.
          </p>
        )}
        {rotate.error && <ErrorAlert>{errorMessage(rotate.error)}</ErrorAlert>}
        {confirming ? (
          <div className="space-y-3">
            <p>
              Resetting makes every earlier copy of your QR code stop working. Your ID stays the
              same.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="danger" disabled={rotate.isPending} onClick={() => rotate.mutate()}>
                {rotate.isPending ? 'Resetting…' : 'Yes, reset my QR'}
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <Button
            variant="secondary"
            onClick={() => {
              setResetDone(false)
              setConfirming(true)
            }}
          >
            Reset my QR
          </Button>
        )}
      </section>
    </Page>
  )
}
