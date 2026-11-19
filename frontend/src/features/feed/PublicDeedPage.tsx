import { Navigate, useParams } from 'react-router'
import { PlaceholderPage } from '../../components/PlaceholderPage'
import { Loading } from '../../components/ui'
import { useAuth } from '../auth/authContext'

/**
 * `/d/:id`, the public share page. A signed-in visitor goes to the member page `/deeds/:id`
 * (§6.2). PLACEHOLDER (M3 F9): the page itself is built in F12.
 */
export function PublicDeedPage() {
  const { id = '' } = useParams()
  const { user, isLoading } = useAuth()
  if (isLoading) return <Loading />
  if (user) return <Navigate to={`/deeds/${encodeURIComponent(id)}`} replace />
  return <PlaceholderPage title="A good deed" task="F12" />
}
