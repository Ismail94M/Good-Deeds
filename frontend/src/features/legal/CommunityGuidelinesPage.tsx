import { Page } from '../../components/ui'

const RULES = [
  {
    title: 'Be kind',
    body: 'Comments are public. Celebrate the people in the deed and speak to others as you would face to face.',
  },
  {
    title: 'No threats, harassment or hate',
    body: 'Don’t threaten, insult or pick on anyone, and never attack people for who they are: religion, caste, gender, language, where they come from, or anything else.',
  },
  {
    title: 'Keep personal details private',
    body: 'Don’t share phone numbers, addresses or other details that could identify someone, including the people who were helped. Links and phone numbers aren’t allowed in comments.',
  },
  {
    title: 'No spam or scams',
    body: 'Don’t advertise, ask for money or post the same thing again and again.',
  },
  {
    title: 'Tell the truth',
    body: 'Only record deeds you saw yourself, and don’t comment to mislead others about a deed.',
  },
]

/**
 * `/community-guidelines` (M3-DESIGN §6.2, §6.3): linked from the comment-removed notice.
 * DRAFT copy: the PO approves it before launch.
 */
export function CommunityGuidelinesPage() {
  return (
    <Page
      title="Community guidelines"
      intro="Good Deeds is a place to recognise kindness. These guidelines keep it that way."
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
        <h2 className="text-lg font-bold text-slate-900">What happens if a comment breaks them</h2>
        <p className="text-slate-700">
          Anyone can report a comment from the “⋯” next to it. Our team reviews every report; the
          comment stays up until they decide. If they remove it, the person who wrote it is told,
          but never who reported it. The people a deed is about, and the person who recorded it, can
          also delete any comment on that deed.
        </p>
      </section>
    </Page>
  )
}
