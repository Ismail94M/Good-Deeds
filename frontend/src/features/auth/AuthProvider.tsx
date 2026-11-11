import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, type ReactNode } from 'react'
import {
  ApiError,
  getAccessToken,
  onSessionExpired,
  refreshAccessToken,
  setAccessToken,
  type TokenResponse,
} from '../../lib/api'
import { AuthContext, type AuthContextValue } from './authContext'
import { identityApi, queryKeys, type Me } from './identityApi'

/** Load the current user, restoring the session from the refresh cookie if needed. */
async function loadMe(): Promise<Me | null> {
  if (!getAccessToken()) {
    const token = await refreshAccessToken()
    if (!token) return null
  }
  try {
    return await identityApi.me()
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

/** Drop cached data that belongs to the previous user (keeps the observed `me` query itself). */
function clearUserData(queryClient: QueryClient) {
  queryClient.removeQueries({
    predicate: (q) => {
      const [root] = q.queryKey
      const isMe = root === 'me' && q.queryKey.length === 1
      return !isMe && root !== 'auth-config' && root !== 'health'
    },
  })
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { data, isPending } = useQuery({
    queryKey: queryKeys.me,
    queryFn: loadMe,
    staleTime: 60_000,
    retry: false,
  })

  useEffect(() => {
    onSessionExpired(() => {
      setAccessToken(null)
      queryClient.setQueryData(queryKeys.me, null)
    })
    return () => onSessionExpired(null)
  }, [queryClient])

  const setMe = useCallback((me: Me) => queryClient.setQueryData(queryKeys.me, me), [queryClient])

  const startSession = useCallback(
    async (tokenRequest: Promise<TokenResponse>) => {
      const { access_token } = await tokenRequest
      setAccessToken(access_token)
      const me = await identityApi.me()
      // Drop anything cached for a previous user before storing the new one.
      clearUserData(queryClient)
      queryClient.setQueryData(queryKeys.me, me)
      return me
    },
    [queryClient],
  )

  const value = useMemo<AuthContextValue>(
    () => ({
      user: data ?? null,
      isLoading: isPending,
      login: (email, password) => startSession(identityApi.login(email, password)),
      signup: (form) => startSession(identityApi.signup(form)),
      oauthLogin: (provider, token) => startSession(identityApi.oauth(provider, token)),
      logout: async () => {
        try {
          await identityApi.logout()
        } catch {
          // Even if the server call fails, forget the session locally.
        }
        setAccessToken(null)
        clearUserData(queryClient)
        queryClient.setQueryData(queryKeys.me, null)
      },
      refreshMe: async () => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.me, exact: true })
        return queryClient.getQueryData<Me | null>(queryKeys.me) ?? null
      },
      setMe,
    }),
    [data, isPending, queryClient, setMe, startSession],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}
