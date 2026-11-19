import { Page } from '../../components/ui'

const RULES = [
  {
    title: 'Ask before you take the photo',
    body: 'Photos are a way to share a good deed proudly. Check that the person you’re recognising is happy to be shown, and anyone else who is clearly in the picture.',
  },
  {
    title: 'Blur anyone who would rather not be shown',
    body: 'If someone doesn’t want to appear, blur their face before you send the report, or leave the photo out. Only the blurred version is ever posted.',
  },
  {
    title: 'Protect the people who were helped',
    body: 'People who were helped are never named on Good Deeds. Don’t post photos that make them easy to identify, especially when they are in a hard moment.',
  },
  {
    title: 'Children need a parent’s consent',
    body: 'Only include a child when their parent or guardian has agreed. If you’re not sure, blur the child’s face or leave the photo out.',
  },
  {
    title: 'Keep private details out',
    body: 'Make sure no phone numbers, house numbers, vehicle number plates, documents or screens with personal details can be read in the photo.',
  },
  {
    title: 'Nothing harmful or misleading',
    body: 'No violence, nudity, injuries shown for shock, or photos that have nothing to do with the deed.',
  },
]

/**
 * `/photo-guidelines` (M3-DESIGN §6.2, Q39): linked from the "removed after review" outcome in
 * My deeds. DRAFT copy: the PO approves it before launch.
 */
export function PhotoGuidelinesPage() {
  return (
    <Page
      title="Photo guidelines"
      intro="Photos help everyone see the good that happens around Bangalore. These guidelines keep them respectful."
    >
      <p
        role="note"
        className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-900"
      >
        Draft — PO to approve
      </p>
      <ol className="space-y-4">
        {RULES.map((rule, index) => (
          <li key={rule.title} className="rounded-2xl border border-slate-200 bg-white p-4">
            <h2 className="font-bold text-slate-900">
              {index + 1}. {rule.title}
            </h2>
            <p className="mt-1 text-slate-700">{rule.body}</p>
          </li>
        ))}
      </ol>
      <section className="space-y-2">
        <h2 className="text-lg font-bold text-slate-900">If a photo is reported</h2>
        <p className="text-slate-700">
          Anyone who sees a photo can report it. Our team reviews every report, and reports about
          children and privacy are looked at first. The photo stays up until they decide. If they
          remove it, the person who posted it is told why, but never who reported it. Removing a
          photo never changes the deed, its verification or anyone’s points.
        </p>
        <p className="text-slate-700">
          The person a deed is about can also remove any photo of their deed at any time, and the
          person who posted a photo can remove it whenever they like.
        </p>
      </section>
    </Page>
  )
}
