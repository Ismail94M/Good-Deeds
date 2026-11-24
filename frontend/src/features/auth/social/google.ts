/** Google Identity Services: renders Google's own button and returns an ID token ("credential"). */

import { loadScript } from './loadScript'

const GIS_SRC = 'https://accounts.google.com/gsi/client'

type CredentialResponse = { credential?: string }

type GoogleAccountsId = {
  initialize: (options: {
    client_id: string
    callback: (response: CredentialResponse) => void
    ux_mode?: 'popup' | 'redirect'
  }) => void
  renderButton: (
    parent: HTMLElement,
    options: {
      type?: 'standard' | 'icon'
      theme?: 'outline' | 'filled_blue' | 'filled_black'
      size?: 'large' | 'medium' | 'small'
      text?: 'signin_with' | 'signup_with' | 'continue_with'
      shape?: 'rectangular' | 'pill'
      width?: number
    },
  ) => void
}

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleAccountsId } }
  }
}

/**
 * Render the "Continue with Google" button into `parent`. `onCredential` receives the ID
 * token to send to POST /auth/oauth/google. Rejects if the SDK can't be loaded.
 */
export async function renderGoogleButton(
  parent: HTMLElement,
  clientId: string,
  onCredential: (credential: string) => void,
  onError: () => void,
): Promise<void> {
  await loadScript(GIS_SRC)
  const gis = window.google?.accounts?.id
  if (!gis) throw new Error('Google sign-in is unavailable')
  gis.initialize({
    client_id: clientId,
    ux_mode: 'popup',
    callback: (response) => {
      if (response.credential) onCredential(response.credential)
      else onError()
    },
  })
  gis.renderButton(parent, {
    type: 'standard',
    theme: 'outline',
    size: 'large',
    text: 'continue_with',
    shape: 'pill',
    width: Math.min(parent.clientWidth || 320, 400),
  })
}
