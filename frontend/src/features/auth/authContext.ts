import { createContext, useContext } from 'react'
import type { Me, OAuthProvider, SignupRequest } from './identityApi'

export type AuthContextValue = {
  /** The signed-in user, or null when logged out. */
  user: Me | null
  /** True while we check for an existing session on app load. */
  isLoading: boolean
  login: (email: string, password: string) => Promise<Me>
  signup: (data: SignupRequest) => Promise<Me>
  oauthLogin: (provider: OAuthProvider, token: string) => Promise<Me>
  logout: () => Promise<void>
  /** Re-fetch /users/me. */
  refreshMe: () => Promise<Me | null>
  /** Store a fresh Me returned by another endpoint (e.g. PATCH /users/me). */
  setMe: (me: Me) => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
