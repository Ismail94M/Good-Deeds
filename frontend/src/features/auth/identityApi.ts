/** Types and calls for the identity API (backend/app/modules/identity). */

import { apiGet, apiPatch, apiPost, type TokenResponse } from '../../lib/api'

export type OnboardingStatus = {
  adult: boolean
  consents: boolean
  phone: boolean
  complete: boolean
}

export type Me = {
  id: string
  email: string | null
  display_name: string
  handle: string
  city: string
  area: string | null
  bio: string | null
  phone_e164: string | null
  role: 'member' | 'moderator' | 'admin'
  /** e.g. "GD-7K3M9Q0". Null until onboarding is complete; never show it before then. */
  short_id: string | null
  created_at: string
  onboarding: OnboardingStatus
}

export type PublicProfile = {
  handle: string
  display_name: string
  city: string
  area: string | null
  bio: string | null
  joined_at: string
}

export type AuthConfig = {
  google_client_id: string | null
  facebook_app_id: string | null
}

export type SignupRequest = {
  email: string
  password: string
  display_name: string
  handle: string
  date_of_birth: string
  accept_terms: true
  accept_privacy: true
}

export type AgeAndConsent = {
  date_of_birth: string
  accept_terms: true
  accept_privacy: true
}

export type ProfileUpdate = {
  display_name?: string
  handle?: string
  area?: string
  bio?: string
}

export type PhoneStartResponse = {
  phone_e164: string
  expires_at: string
  resend_after_seconds: number
}

export type QrCode = {
  short_id: string
  display_id: string
  token: string
  scan_url: string
  expires_at: string
  refresh_after_seconds: number
}

export type OAuthProvider = 'google' | 'facebook'

export const identityApi = {
  config: () => apiGet<AuthConfig>('/auth/config'),
  signup: (data: SignupRequest) => apiPost<TokenResponse>('/auth/signup', data),
  login: (email: string, password: string) =>
    apiPost<TokenResponse>('/auth/login', { email, password }),
  oauth: (provider: OAuthProvider, token: string) =>
    apiPost<TokenResponse>(`/auth/oauth/${provider}`, { token }),
  logout: () => apiPost<void>('/auth/logout'),
  me: () => apiGet<Me>('/users/me'),
  updateMe: (data: ProfileUpdate) => apiPatch<Me>('/users/me', data),
  ageConsent: (data: AgeAndConsent) => apiPost<Me>('/users/me/age-consent', data),
  phoneStart: (phone: string) => apiPost<PhoneStartResponse>('/users/me/phone/start', { phone }),
  phoneVerify: (phone: string, code: string) =>
    apiPost<Me>('/users/me/phone/verify', { phone, code }),
  qr: () => apiGet<QrCode>('/users/me/qr'),
  rotateQr: () => apiPost<QrCode>('/users/me/qr/rotate'),
  profile: (handle: string) => apiGet<PublicProfile>(`/users/${encodeURIComponent(handle)}`),
}

export const queryKeys = {
  me: ['me'] as const,
  authConfig: ['auth-config'] as const,
  qr: ['me', 'qr'] as const,
  profile: (handle: string) => ['profile', handle.toLowerCase()] as const,
}
