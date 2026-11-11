import { LinkButton, Page } from '../components/ui'

export function NotFoundPage() {
  return (
    <Page title="Page not found" intro="We couldn't find that page.">
      <LinkButton to="/" variant="secondary">
        Go home
      </LinkButton>
    </Page>
  )
}
