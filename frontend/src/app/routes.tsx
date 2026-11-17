import type { RouteObject } from 'react-router'
import { ForgotPasswordPage } from '../features/auth/ForgotPasswordPage'
import { LoginPage } from '../features/auth/LoginPage'
import { SignupPage } from '../features/auth/SignupPage'
import { DeedDetailPage } from '../features/deeds/DeedDetailPage'
import { EditDeedPage } from '../features/deeds/EditDeedPage'
import { MyDeedsPage } from '../features/deeds/MyDeedsPage'
import { HomePage } from '../features/home/HomePage'
import { PrivacyPage, TermsPage } from '../features/legal/LegalPages'
import { OnboardingPage } from '../features/onboarding/OnboardingPage'
import { ProfilePage } from '../features/profile/ProfilePage'
import { PublicProfilePage } from '../features/profile/PublicProfilePage'
import { MyQrPage } from '../features/qr/MyQrPage'
import { ManualCodePage } from '../features/report/ManualCodePage'
import { ReportHubPage } from '../features/report/ReportHubPage'
import { ReportLayout } from '../features/report/ReportLayout'
import { ScanCameraPage } from '../features/report/ScanCameraPage'
import { ScanDeedPage } from '../features/report/ScanDeedPage'
import { ScanLandingPage, ScanLinkPage } from '../features/report/ScanLandingPage'
import { SelfLogFormPage } from '../features/selflog/SelfLogFormPage'
import { SelfLogsPage } from '../features/selflog/SelfLogsPage'
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
          { path: '/forgot-password', element: <ForgotPasswordPage /> },
        ],
      },
      {
        element: <RequireAuth />,
        children: [
          { path: '/onboarding', element: <OnboardingPage /> },
          { path: '/profile', element: <ProfilePage /> },
          // My deeds (M2-DESIGN §5.2): open to any signed-in user, like Profile.
          { path: '/me/deeds', element: <MyDeedsPage /> },
          { path: '/me/deeds/:id', element: <DeedDetailPage /> },
        ],
      },
      {
        element: <RequireOnboarded />,
        children: [
          { path: '/me/qr', element: <MyQrPage /> },
          { path: '/me/deeds/:id/edit', element: <EditDeedPage /> },
          { path: '/me/self-logs', element: <SelfLogsPage /> },
          { path: '/me/self-logs/new', element: <SelfLogFormPage /> },
          { path: '/me/self-logs/:id/edit', element: <SelfLogFormPage /> },
          {
            // Record a deed (M2-DESIGN §5.2). The layout keeps the witness's position while
            // they move between these pages.
            element: <ReportLayout />,
            children: [
              { path: '/report', element: <ReportHubPage /> },
              { path: '/report/scan', element: <ScanCameraPage /> },
              { path: '/report/code', element: <ManualCodePage /> },
              { path: '/report/scan/:scanId', element: <ScanDeedPage /> },
              { path: '/scan/:token', element: <ScanLandingPage /> },
              { path: '/report/link', element: <ScanLinkPage /> },
            ],
          },
        ],
      },
      { path: '/u/:handle', element: <PublicProfilePage /> },
      { path: '/terms', element: <TermsPage /> },
      { path: '/privacy', element: <PrivacyPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
