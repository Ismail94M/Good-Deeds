import type { RouteObject } from 'react-router'
import { LoginPage } from '../features/auth/LoginPage'
import { SignupPage } from '../features/auth/SignupPage'
import { HomePage } from '../features/home/HomePage'
import { PrivacyPage, TermsPage } from '../features/legal/LegalPages'
import { OnboardingPage } from '../features/onboarding/OnboardingPage'
import { ProfilePage } from '../features/profile/ProfilePage'
import { PublicProfilePage } from '../features/profile/PublicProfilePage'
import { MyQrPage } from '../features/qr/MyQrPage'
import { ScanPage } from '../features/scan/ScanPage'
import { NotFoundPage } from './NotFoundPage'
import { RedirectIfAuthed, RequireAuth, RequireOnboarded } from './guards'
import { Layout } from './Layout'

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { path: '/', element: <HomePage /> },
      {
        element: <RedirectIfAuthed />,
        children: [
          { path: '/signup', element: <SignupPage /> },
          { path: '/login', element: <LoginPage /> },
        ],
      },
      {
        element: <RequireAuth />,
        children: [
          { path: '/onboarding', element: <OnboardingPage /> },
          { path: '/profile', element: <ProfilePage /> },
        ],
      },
      {
        element: <RequireOnboarded />,
        children: [{ path: '/me/qr', element: <MyQrPage /> }],
      },
      { path: '/u/:handle', element: <PublicProfilePage /> },
      { path: '/scan/:token', element: <ScanPage /> },
      { path: '/terms', element: <TermsPage /> },
      { path: '/privacy', element: <PrivacyPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
