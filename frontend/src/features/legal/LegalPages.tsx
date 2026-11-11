import type { ReactNode } from 'react'
import { Page } from '../../components/ui'

const CONTACT_EMAIL = 'privacy@gooddeeds.example'

function DraftBanner() {
  return (
    <p
      role="note"
      className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 font-semibold text-amber-900"
    >
      Draft, pending legal review. This text is a placeholder and may change before launch.
    </p>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  )
}

export function TermsPage() {
  return (
    <Page title="Terms of Use">
      <DraftBanner />
      <Section title="Who can use Good Deeds">
        <p>You must be 18 or older and live in or visit Bangalore to take part in v1.</p>
      </Section>
      <Section title="Be honest and kind">
        <p>
          Only report good deeds you really saw. Don&apos;t stage deeds, report yourself, or team up
          with others to game points. Never share photos that identify the people being helped
          without their consent.
        </p>
      </Section>
      <Section title="Your account">
        <p>
          Keep your login safe. One account per person, with one verified phone number. We may
          suspend accounts that break these rules.
        </p>
      </Section>
    </Page>
  )
}

export function PrivacyPage() {
  return (
    <Page
      title="Privacy Notice"
      intro="What we collect, why, and your rights under India's Digital Personal Data Protection Act, 2023."
    >
      <DraftBanner />
      <Section title="What we collect and why">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong>Email address:</strong> to create your account, log you in and contact you about
            it.
          </li>
          <li>
            <strong>Phone number:</strong> to verify you are a real person and to prevent fake or
            duplicate accounts. We never show it to other people.
          </li>
          <li>
            <strong>A device identifier:</strong> a random ID stored in your browser, used to
            prevent fraud (for example, one person pretending to be many).
          </li>
          <li>
            <strong>Date of birth:</strong> only used to confirm you are 18 or older. We do not
            store it.
          </li>
          <li>
            <strong>Your profile:</strong> the name, handle, area and bio you choose to share. These
            are public.
          </li>
          <li>
            <strong>Coming in later versions:</strong> the location and photos or videos of good
            deeds you report. We will ask for your consent before collecting these.
          </li>
        </ul>
      </Section>
      <Section title="Your rights">
        <p>
          You can ask to see the personal data we hold about you, correct it, or delete your account
          and data. Email{' '}
          <a className="font-medium text-brand-700 underline" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>{' '}
          (placeholder address) and we will reply within the time the law requires.
        </p>
      </Section>
      <Section title="Withdrawing consent">
        <p>
          You can withdraw your consent at any time by asking us to delete your account. Some
          records (such as fraud-prevention logs) may be kept for as long as the law requires.
        </p>
      </Section>
    </Page>
  )
}
