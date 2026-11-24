import { useCallback, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ApiError } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { ONBOARDING_PROMPT } from './feedText'

/**
 * Social actions need a finished setup (D44, Q42 = A): the feed is readable before onboarding,
 * and a like, save, comment or follow shows this prompt instead. Returns `guard(action)`, which
 * runs the action only for an onboarded user, `handleError(error)`, which shows the prompt on a
 * 403 `onboarding_required` and returns true, and the `prompt` element to render.
 */
export function useOnboardingGate(): {
  ready: boolean
  guard: (action: () => void) => void
  handleError: (error: unknown) => boolean
  prompt: ReactNode
} {
  const { user } = useAuth()
  const ready = Boolean(user?.onboarding.complete)
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()

  const guard = useCallback(
    (action: () => void) => {
      if (ready) action()
      else setOpen(true)
    },
    [ready],
  )

  const handleError = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.code === 'onboarding_required') {
      setOpen(true)
      return true
    }
    return false
  }, [])

  const here = encodeURIComponent(location.pathname + location.search)
  const prompt = open ? (
    <ConfirmDialog
      title="Finish setting up"
      body={ONBOARDING_PROMPT}
      onDismiss={() => setOpen(false)}
      actions={[
        {
          label: 'Finish setting up',
          variant: 'primary',
          autoFocus: true,
          onClick: () => navigate(`/onboarding?next=${here}`),
        },
        { label: 'Not now', variant: 'secondary', onClick: () => setOpen(false) },
      ]}
    />
  ) : null

  return { ready, guard, handleError, prompt }
}
