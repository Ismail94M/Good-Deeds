import type { RouteObject } from 'react-router'
import { CommentReportReviewPage } from '../features/admin/CommentReportReviewPage'
import { CommentReportsPage } from '../features/admin/CommentReportsPage'
import { PhotoReportReviewPage } from '../features/admin/PhotoReportReviewPage'
import { PhotoReportsPage } from '../features/admin/PhotoReportsPage'
import { ForgotPasswordPage } from '../features/auth/ForgotPasswordPage'
import { LoginPage } from '../features/auth/LoginPage'
import { SignupPage } from '../features/auth/SignupPage'
import { DeedDetailPage } from '../features/deeds/DeedDetailPage'
import { EditDeedPage } from '../features/deeds/EditDeedPage'
import { MyDeedsPage } from '../features/deeds/MyDeedsPage'
import { ConnectionsPage } from '../features/feed/ConnectionsPage'
import { DeedPage } from '../features/feed/DeedPage'
import { PublicDeedPage } from '../features/feed/PublicDeedPage'
import { SavedPage } from '../features/feed/SavedPage'
import { HomePage } from '../features/home/HomePage'
import { CommunityGuidelinesPage } from '../features/legal/CommunityGuidelinesPage'
import { PrivacyPage, TermsPage } from '../features/legal/LegalPages'
import { PhotoGuidelinesPage } from '../features/legal/PhotoGuidelinesPage'
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
import { RedirectIfAuthed, RequireAuth, RequireOnboarded, RequireRole } from './guards'
import { Layout } from './Layout'

const ADMIN_ROLES = ['moderator', 'admin'] as const

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
          // M3 feed (M3-DESIGN §6.2): readable before onboarding; writes prompt to finish (D44).
          { path: '/deeds/:id', element: <DeedPage /> },
          { path: '/me/saved', element: <SavedPage /> },
          { path: '/me/connections', element: <ConnectionsPage /> },
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
      {
        // Report review (M3-DESIGN §6.2): moderators and admins (Q34); others see NotFound.
        element: <RequireRole roles={ADMIN_ROLES} />,
        children: [
          { path: '/admin/photo-reports', element: <PhotoReportsPage /> },
          { path: '/admin/photo-reports/:caseId', element: <PhotoReportReviewPage /> },
          { path: '/admin/comment-reports', element: <CommentReportsPage /> },
          { path: '/admin/comment-reports/:caseId', element: <CommentReportReviewPage /> },
        ],
      },
      { path: '/d/:id', element: <PublicDeedPage /> },
      { path: '/u/:handle', element: <PublicProfilePage /> },
      { path: '/community-guidelines', element: <CommunityGuidelinesPage /> },
      { path: '/photo-guidelines', element: <PhotoGuidelinesPage /> },
      { path: '/terms', element: <TermsPage /> },
      { path: '/privacy', element: <PrivacyPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
