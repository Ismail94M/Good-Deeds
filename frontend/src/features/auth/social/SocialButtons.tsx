import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { errorMessage } from '../../../lib/api'
import { useAuth } from '../authContext'
import { identityApi, queryKeys, type Me, type OAuthProvider } from '../identityApi'
import { facebookLogin, prepareFacebook } from './facebook'
import { renderGoogleButton } from './google'

const UNAVAILABLE = "This sign-in option isn't available right now. Please use email instead."

/** "Continue with Google / Facebook". Shows nothing when neither provider is configured. */
export function SocialButtons({ onSuccess }: { onSuccess?: (me: Me) => void }) {
  const { data: config } = useQuery({
    queryKey: queryKeys.authConfig,
    queryFn: identityApi.config,
    staleTime: Infinity,
    retry: false,
  })
  const { oauthLogin } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const googleId = config?.google_client_id ?? null
  const facebookId = config?.facebook_app_id ?? null
  if (!googleId && !facebookId) return null

  async function finish(provider: OAuthProvider, token: string) {
    setError(null)
    setBusy(true)
    try {
      const me = await oauthLogin(provider, token)
      onSuccess?.(me)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-label="Other ways to sign in" className="space-y-3">
      <p className="flex items-center gap-3 text-sm text-stone-500">
        <span aria-hidden className="h-px flex-1 bg-stone-200" />
        or
        <span aria-hidden className="h-px flex-1 bg-stone-200" />
      </p>
      {googleId && (
        <GoogleButton
          clientId={googleId}
          onToken={(t) => void finish('google', t)}
          onError={() => setError(UNAVAILABLE)}
        />
      )}
      {facebookId && (
        <FacebookButton
          appId={facebookId}
          disabled={busy}
          onToken={(t) => void finish('facebook', t)}
          onError={(message) => setError(message)}
        />
      )}
      {busy && (
        <p role="status" className="text-sm text-stone-600">
          Signing you in…
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
    </section>
  )
}

function GoogleButton({
  clientId,
  onToken,
  onError,
}: {
  clientId: string
  onToken: (token: string) => void
  onError: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  // Keep the latest callbacks without re-rendering Google's button on every render.
  const callbacks = useRef({ onToken, onError })
  useEffect(() => {
    callbacks.current = { onToken, onError }
  })

  useEffect(() => {
    const el = ref.current
    if (!el) return
    renderGoogleButton(
      el,
      clientId,
      (credential) => callbacks.current.onToken(credential),
      () => callbacks.current.onError(),
    ).catch(() => callbacks.current.onError())
  }, [clientId])

  return <div ref={ref} className="flex min-h-11 justify-center" data-testid="google-button" />
}

function FacebookButton({
  appId,
  disabled,
  onToken,
  onError,
}: {
  appId: string
  disabled: boolean
  onToken: (token: string) => void
  onError: (message: string) => void
}) {
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    prepareFacebook(appId)
      .then(() => active && setReady(true))
      .catch(() => active && setFailed(true))
    return () => {
      active = false
    }
  }, [appId])

  async function handleClick() {
    if (!ready) {
      onError(UNAVAILABLE)
      return
    }
    try {
      const token = await facebookLogin()
      if (token) onToken(token)
    } catch {
      onError(UNAVAILABLE)
    }
  }

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={disabled || failed}
      className="flex min-h-11 w-full items-center justify-center rounded-full bg-[#1877f2] px-4 font-semibold text-white hover:bg-[#166fe0] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#1877f2] disabled:opacity-60"
    >
      {failed ? 'Facebook sign-in unavailable' : 'Continue with Facebook'}
    </button>
  )
}
