/**
 * M5 F25, member side (docs/M5-DESIGN.md §3.3b M1–M4, §6.2 "Reviews (member)", D99–D101): the
 * Reviews page (explanation reply with limits, decision notices with server texts, appeal),
 * `POST /reviews/seen`, the Profile dot, the "Removed after review" link in My deeds, and that the
 * page never says "fraud".
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makeOptions } from '../report/testFixtures'
import type { MyExplanation, MyNotice, MyReviews } from './reviewsApi'

const RAVI = { handle: 'ravi', display_name: 'Ravi Kumar' }

function makeExplanation(overrides: Partial<MyExplanation> = {}): MyExplanation {
  return {
    id: 'ex-1',
    requested_at: '2026-11-20T06:00:00Z',
    reply_due_at: '2026-12-04T06:00:00Z',
    replied_at: null,
    reply: null,
    status: 'waiting',
    outcome_text: null,
    deeds: [
      {
        deed_id: 'deed-1',
        role: 'doer',
        category_label: 'Helping elderly',
        area_name: 'Koramangala',
        occurred_on: '2026-11-10',
        other_person: RAVI,
      },
    ],
    ...overrides,
  }
}

function makeNotice(overrides: Partial<MyNotice> = {}): MyNotice {
  return {
    id: 'no-1',
    created_at: '2026-11-22T06:00:00Z',
    reason_text:
      'These recognitions didn’t meet our rule that recognitions should come from independent witnesses',
    items: [
      {
        deed: {
          deed_id: 'deed-2',
          role: 'reporter',
          category_label: 'Cleaning up',
          area_name: 'Indiranagar',
          occurred_on: '2026-11-05',
          other_person: RAVI,
        },
        effect_text: 'Will be removed on 22 December 2026 unless you ask for another review',
      },
    ],
    appeal_until: '2026-12-22T06:00:00Z',
    can_appeal: true,
    appeal: null,
    final: false,
    ...overrides,
  }
}

function makeReviews(overrides: Partial<MyReviews> = {}): MyReviews {
  return {
    explanations: [makeExplanation()],
    notices: [makeNotice()],
    unread: 2,
    limits: { reply_max_chars: 40, appeal_reason_min_chars: 10, appeal_reason_max_chars: 60 },
    ...overrides,
  }
}

function base(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(makeMe()),
    'GET /reviews': { body: makeReviews() },
    'POST /reviews/seen': { status: 204 },
    ...extra,
  })
}

describe('member Reviews page', () => {
  it('shows the request, the notice with server texts, and marks them seen', async () => {
    const api = base()
    const { container } = renderApp('/me/reviews')
    expect(await screen.findByRole('heading', { name: 'Reviews' })).toBeInTheDocument()
    expect(
      await screen.findByText(
        'We’re taking a closer look at some of your recent deeds. Most of the time everything is fine. If you’d like, tell us about them.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Ravi Kumar recognised you')).toBeInTheDocument()
    expect(screen.getByText('Helping elderly · 10 November 2026 · Koramangala')).toBeInTheDocument()
    expect(screen.getByText('Reply by 4 December 2026')).toBeInTheDocument()
    expect(screen.getByText(makeNotice().reason_text)).toBeInTheDocument()
    expect(screen.getByText('You recognised Ravi Kumar')).toBeInTheDocument()
    expect(screen.getByText(makeNotice().items[0].effect_text)).toBeInTheDocument()
    expect(screen.getByText('You can ask until 22 December 2026.')).toBeInTheDocument()
    await waitFor(() => expect(api.calls('POST /reviews/seen')).toHaveLength(1))
    // The Profile dot goes once seen.
    await waitFor(() => expect(screen.queryByText('(new review update)')).not.toBeInTheDocument())
    expect(container.textContent?.toLowerCase()).not.toContain('fraud')
  })

  it('sends a reply within the limit and thanks the person', async () => {
    const api = base({
      'POST /reviews/explanations/ex-1/reply': {
        body: makeExplanation({
          status: 'replied',
          reply: 'I help him every week',
          replied_at: '2026-11-26T06:00:00Z',
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/me/reviews')
    const box = await screen.findByLabelText(/Your reply/)
    expect(box).toHaveAttribute('maxLength', '40')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await user.type(box, 'I help him every week')
    expect(screen.getByText('21 / 40')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Thanks, we’ve got your reply.')).toBeInTheDocument()
    expect(screen.getByText('I help him every week')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Your reply/)).not.toBeInTheDocument()
    expect(api.calls('POST /reviews/explanations/ex-1/reply')[0].body).toEqual({
      reply: 'I help him every week',
    })
  })

  it('shows a closed request’s server outcome and no form', async () => {
    base({
      'GET /reviews': {
        body: makeReviews({
          explanations: [
            makeExplanation({
              status: 'closed',
              outcome_text: 'We’ve finished looking. No changes.',
            }),
          ],
          notices: [],
        }),
      },
    })
    renderApp('/me/reviews')
    expect(await screen.findByText('We’ve finished looking. No changes.')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Your reply/)).not.toBeInTheDocument()
  })

  it('shows reply errors', async () => {
    base({
      'POST /reviews/explanations/ex-1/reply': {
        status: 409,
        body: { detail: 'x', code: 'reply_closed' },
      },
    })
    const user = userEvent.setup()
    renderApp('/me/reviews')
    await user.type(await screen.findByLabelText(/Your reply/), 'Hello')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The time to reply has passed.')
  })

  it('asks for another review with a reason inside the limits', async () => {
    const api = base({
      'POST /reviews/notices/no-1/appeal': {
        body: makeNotice({
          can_appeal: false,
          appeal: {
            created_at: '2026-11-26T06:00:00Z',
            reason: 'We are neighbours, it was real',
            outcome: null,
            outcome_text: null,
            decided_at: null,
          },
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/me/reviews')
    await user.click(await screen.findByRole('button', { name: 'Ask for another review' }))
    const box = screen.getByLabelText('Why should we look again?')
    expect(box).toHaveAttribute('maxLength', '60')
    await user.type(box, 'Too short')
    await user.click(screen.getByRole('button', { name: 'Ask for another review' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Tell us why in at least 10 characters.')
    expect(api.calls('POST /reviews/notices/no-1/appeal')).toHaveLength(0)
    await user.clear(box)
    await user.type(box, 'We are neighbours, it was real')
    await user.click(screen.getByRole('button', { name: 'Ask for another review' }))
    expect(await screen.findByText('We’ll show the answer here.')).toBeInTheDocument()
    expect(api.calls('POST /reviews/notices/no-1/appeal')[0].body).toEqual({
      reason: 'We are neighbours, it was real',
    })
  })

  it('shows appeal errors', async () => {
    base({
      'POST /reviews/notices/no-1/appeal': {
        status: 409,
        body: { detail: 'x', code: 'already_appealed' },
      },
    })
    const user = userEvent.setup()
    renderApp('/me/reviews')
    await user.click(await screen.findByRole('button', { name: 'Ask for another review' }))
    await user.type(screen.getByLabelText('Why should we look again?'), 'Please look again')
    await user.click(screen.getByRole('button', { name: 'Ask for another review' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You’ve already asked for another review.',
    )
  })

  it('shows a decided appeal’s server outcome', async () => {
    base({
      'GET /reviews': {
        body: makeReviews({
          explanations: [],
          notices: [
            makeNotice({
              can_appeal: false,
              final: true,
              appeal: {
                created_at: '2026-11-23T06:00:00Z',
                reason: 'It was real',
                outcome: 'overturned',
                outcome_text: 'We looked again and restored your deeds',
                decided_at: '2026-11-25T06:00:00Z',
              },
            }),
          ],
        }),
      },
    })
    renderApp('/me/reviews')
    expect(await screen.findByText('We looked again and restored your deeds')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask for another review' })).not.toBeInTheDocument()
  })

  it('shows an empty state', async () => {
    base({ 'GET /reviews': { body: makeReviews({ explanations: [], notices: [], unread: 0 }) } })
    renderApp('/me/reviews')
    expect(await screen.findByRole('heading', { name: 'Nothing to review' })).toBeInTheDocument()
  })

  it('never renders the word fraud, whatever the page shows', async () => {
    base({
      'GET /reviews': {
        body: makeReviews({
          explanations: [
            makeExplanation(),
            makeExplanation({ id: 'ex-2', status: 'replied', reply: 'Hello there' }),
            makeExplanation({ id: 'ex-3', status: 'closed', outcome_text: 'No changes.' }),
          ],
          notices: [
            makeNotice(),
            makeNotice({
              id: 'no-2',
              can_appeal: false,
              appeal: {
                created_at: '2026-11-23T06:00:00Z',
                reason: 'Please',
                outcome: 'upheld',
                outcome_text: 'We looked again and the decision stays',
                decided_at: '2026-11-25T06:00:00Z',
              },
            }),
          ],
        }),
      },
    })
    const user = userEvent.setup()
    const { container } = renderApp('/me/reviews')
    await user.click(await screen.findByRole('button', { name: 'Ask for another review' }))
    expect(container.textContent?.toLowerCase()).not.toMatch(/fraud|flag|case|ring/)
  })
})

describe('Profile dot and links', () => {
  it('shows a dot on Profile while unread > 0, and a Reviews link on the profile', async () => {
    base({
      'GET /scoring/me': { status: 404, body: { detail: 'x' } },
    })
    renderApp('/profile')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(await within(nav).findByText('(new review update)')).toBeInTheDocument()
    const link = await screen.findByRole('link', { name: /Reviews/ })
    expect(link).toHaveAttribute('href', '/me/reviews')
    expect(link).toHaveTextContent('(new update)')
  })

  it('shows no dot when nothing is unread', async () => {
    const api = base({ 'GET /reviews': { body: makeReviews({ unread: 0 }) } })
    renderApp('/profile')
    await screen.findByRole('navigation', { name: 'Main' })
    await waitFor(() => expect(api.calls('GET /reviews').length).toBeGreaterThan(0))
    expect(screen.queryByText('(new review update)')).not.toBeInTheDocument()
  })

  it('does not ask for reviews when signed out', async () => {
    const api = mockApi({})
    renderApp('/')
    await screen.findAllByRole('link', { name: /Sign in|Create an account/ })
    expect(api.calls('GET /reviews')).toHaveLength(0)
  })

  it('links a rejected deed in My deeds to its notice as "Removed after review"', async () => {
    const deed = {
      id: 'deed-2',
      status: 'rejected',
      verification_method: 'qr',
      category: 'cleaning',
      description: 'Cleaned the park',
      beneficiaries_count: null,
      occurred_at: '2026-11-05T06:00:00Z',
      reported_at: '2026-11-05T06:00:00Z',
      verified_at: '2026-11-05T06:01:00Z',
      withdrawn_at: null,
      last_edited_at: null,
      reporter: { handle: 'asha', display_name: 'Asha' },
      doer: RAVI,
      same_act: null,
      prize_rank_eligible: false,
      has_location: true,
      area_name: 'Indiranagar',
      can_edit: false,
      can_withdraw: false,
      withdrawable_until: null,
    }
    base({
      'GET /deeds/options': { body: makeOptions() },
      'GET /me/deeds': { body: { items: [deed], next_cursor: null } },
    })
    renderApp('/me/deeds?tab=reported')
    const link = await screen.findByRole('link', { name: 'Removed after review' })
    await waitFor(() => expect(link).toHaveAttribute('href', '/me/reviews#notice-no-1'))
  })
})
