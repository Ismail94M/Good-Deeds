import { LinkButton, Page } from '../../components/ui'

/** Where a scanned QR code lands. Recognising deeds is built in the next milestone (M2). */
export function ScanPage() {
  return (
    <Page
      title="You scanned a Good Deeds code"
      intro="Thank you for noticing kindness! Recognising good deeds arrives in the next update."
    >
      <p className="text-slate-600">
        Soon you&apos;ll be able to tell everyone about the good deed you saw, right from here.
      </p>
      <LinkButton to="/" variant="secondary">
        Go home
      </LinkButton>
    </Page>
  )
}
