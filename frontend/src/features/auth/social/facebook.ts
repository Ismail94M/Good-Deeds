/** Facebook JS SDK: returns a user access token to send to POST /auth/oauth/facebook. */

import { loadScript } from './loadScript'

const FB_SRC = 'https://connect.facebook.net/en_US/sdk.js'
const FB_VERSION = 'v19.0'

type FbLoginResponse = {
  status?: string
  authResponse?: { accessToken?: string } | null
}

type FacebookSdk = {
  init: (options: { appId: string; version: string; cookie?: boolean; xfbml?: boolean }) => void
  login: (callback: (response: FbLoginResponse) => void, options?: { scope?: string }) => void
}

declare global {
  interface Window {
    FB?: FacebookSdk
  }
}

let initialisedFor: string | null = null

/** Load and initialise the SDK ahead of time, so the login popup opens straight from a tap. */
export async function prepareFacebook(appId: string): Promise<void> {
  await loadScript(FB_SRC)
  const fb = window.FB
  if (!fb) throw new Error('Facebook sign-in is unavailable')
  if (initialisedFor !== appId) {
    fb.init({ appId, version: FB_VERSION, cookie: false, xfbml: false })
    initialisedFor = appId
  }
}

/**
 * Open the Facebook login popup. Call from a click handler after prepareFacebook() resolved.
 * Resolves to the access token, or null if the person cancelled.
 */
export function facebookLogin(): Promise<string | null> {
  const fb = window.FB
  if (!fb || !initialisedFor) return Promise.reject(new Error('Facebook sign-in is not ready'))
  return new Promise((resolve) => {
    fb.login((response) => resolve(response.authResponse?.accessToken ?? null), {
      scope: 'email',
    })
  })
}
