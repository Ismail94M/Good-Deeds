/**
 * M5 F22/F23 (docs/M5-DESIGN.md §3.4 #9, #13–#18, §6.2 "Deed scoring", "User scoring"): the deed
 * scoring page (held banner, overrides, override form 200 vs 202, error copy, final-month
 * banner, NotFound on 404), the override-requests queue (approve / decline, conflicts) and the
 * user scoring page (revoke gating and errors).
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import type { AdminDeedScoring, OverrideRequest, AdminUserScoring } from './scoringAdminApi'
import { AHMED, MOD, PRIYA } from './testFixtures'

const FRAUD_OPTIONS = {
  types: [],
  statuses: ['open', 'parked', 'resolved'],
  outcomes: ['confirmed', 'dismissed'],
  deed_outcomes: ['exclude', 'reject', 'release'],
  note_min_chars: 10,
  note_max_chars: 500,
  override_reason_codes: [
    { id: 'fake_photo', label: 'Fake photo' },
    { id: 'rule_misfire', label: 'Rule misfire' },
  ],
  override_max_points: 50,
  override_second_admin: { max_cut_points: 10, cut_to_zero: true, expire_days: 14 },
  explanation_reply_days: 14,
  appeal_window_days: 30,
}

function makeDeed(overrides: Partial<AdminDeedScoring> = {}): AdminDeedScoring {
  return {
    deed_id: 'deed-1',
    held: false,
    period_month: '2026-10',
    month_final: false,
    ledger: [
      {
        id: 'l1',
        created_at: '2026-10-10T06:00:00Z',
        deed_id: 'deed-1',
        role: 'doer',
        kind: 'award',
        points: 12,
        reason: 'verified',
        reason_label: 'Verified deed',
        provisional: false,
        provisional_until: null,
        category_label: 'Helping elderly',
        doer: AHMED,
        reporter: PRIYA,
        breakdown: {},
        config_version: 1,
        reverses_entry_id: null,
      },
    ],
    evaluations: [],
    overrides: [],
    ...overrides,
  }
}

function makeRequest(overrides: Partial<OverrideRequest> = {}): OverrideRequest {
  return {
    id: 'req-1',
    deed_id: 'deed-1',
    role: 'doer',
    user: AHMED,
    previous_points: 12,
    points: 0,
    reason_code: 'fake_photo',
    note: 'Photo is from the internet',
    case_id: null,
    requested_by: { handle: 'ravi', display_name: 'Ravi Admin' },
    created_at: '2026-11-25T06:00:00Z',
    expires_at: '2026-12-09T06:00:00Z',
    status: 'pending',
    decided_by: null,
    decision_note: null,
    decided_at: null,
    override_id: null,
    ...overrides,
  }
}

const FINAL_MONTH_TEXT =
  'This month is final: the change shows in the person’s points but not on the final leaderboard.'

function base(extra: Parameters<typeof mockApi>[0] = {}, role: 'admin' | 'moderator' = 'admin') {
  return mockApi({
    ...signedIn(makeMe({ role, handle: MOD.handle })),
    'GET /admin/fraud/options': { body: FRAUD_OPTIONS },
    ...extra,
  })
}

async function fillForm(points: string | null, note = 'Photo shows a different act') {
  const user = userEvent.setup()
  await screen.findByRole('heading', { name: 'Change the points' })
  if (points === null) {
    await user.click(screen.getByRole('radio', { name: 'Back to the rules' }))
  } else {
    await user.type(screen.getByLabelText(/Points \(0 to 50\)/), points)
  }
  await user.selectOptions(screen.getByLabelText('Reason'), 'fake_photo')
  await user.type(screen.getByLabelText(/Note for the record/), note)
  await user.click(screen.getByRole('button', { name: 'Save the change' }))
  return user
}

describe('deed scoring admin page', () => {
  it('shows the ledger, the held banner and the overrides history', async () => {
    base({
      'GET /admin/scoring/deeds/deed-1': {
        body: makeDeed({
          held: true,
          overrides: [
            {
              id: 'o1',
              role: 'doer',
              points: 8,
              previous_points: 12,
              reason_code: 'rule_misfire',
              note: 'Bonus was wrong',
              actor: { handle: 'ravi', display_name: 'Ravi Admin' },
              case_id: null,
              request_id: null,
              created_at: '2026-11-20T06:00:00Z',
            },
          ],
        }),
      },
    })
    renderApp('/admin/scoring/deeds/deed-1')
    expect(await screen.findByRole('heading', { name: 'Deed points' })).toBeInTheDocument()
    expect(screen.getByText('Held for review')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Verified deed' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '+12' })).toBeInTheDocument()
    expect(await screen.findByText('Doer: 12 → 8')).toBeInTheDocument()
    expect(screen.getByText(/Rule misfire · by Ravi Admin/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Ahmed Khan' })).toHaveAttribute(
      'href',
      '/admin/users/ahmedk',
    )
    expect(screen.queryByText(/This month is final/)).not.toBeInTheDocument()
  })

  it('applies an override (200) and shows the new data', async () => {
    const api = base({
      'GET /admin/scoring/deeds/deed-1': { body: makeDeed() },
      'POST /admin/scoring/deeds/deed-1/overrides': {
        body: makeDeed({
          overrides: [
            {
              id: 'o2',
              role: 'doer',
              points: 20,
              previous_points: 12,
              reason_code: 'fake_photo',
              note: 'Photo shows a different act',
              actor: MOD,
              case_id: null,
              request_id: null,
              created_at: '2026-11-26T06:00:00Z',
            },
          ],
        }),
      },
    })
    renderApp('/admin/scoring/deeds/deed-1')
    await fillForm('20')
    expect(await screen.findByRole('status')).toHaveTextContent('Points changed.')
    expect(screen.getByText('Doer: 12 → 20')).toBeInTheDocument()
    expect(api.calls('POST /admin/scoring/deeds/deed-1/overrides')[0].body).toEqual({
      role: 'doer',
      points: 20,
      reason_code: 'fake_photo',
      note: 'Photo shows a different act',
      case_id: null,
    })
  })

  it('sends a big cut for a second admin (202)', async () => {
    const api = base({
      'GET /admin/scoring/deeds/deed-1': { body: makeDeed() },
      'POST /admin/scoring/deeds/deed-1/overrides': { status: 202, body: makeRequest() },
    })
    renderApp('/admin/scoring/deeds/deed-1')
    await fillForm('0')
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Sent for a second admin’s approval: cuts to 0 or by more than 10 points need one.',
    )
    expect(screen.getByText('No overrides yet.')).toBeInTheDocument()
    expect(api.calls('POST /admin/scoring/deeds/deed-1/overrides')[0].body).toMatchObject({
      points: 0,
    })
  })

  it('"Back to the rules" sends points null', async () => {
    const api = base({
      'GET /admin/scoring/deeds/deed-1': { body: makeDeed() },
      'POST /admin/scoring/deeds/deed-1/overrides': { body: makeDeed() },
    })
    renderApp('/admin/scoring/deeds/deed-1')
    await fillForm(null)
    await screen.findByRole('status')
    expect(api.calls('POST /admin/scoring/deeds/deed-1/overrides')[0].body).toMatchObject({
      points: null,
    })
  })

  it.each([
    [
      'nothing_to_override',
      'This report earns no points to change: it is capped, not counted or no longer verified.',
    ],
    ['request_pending', 'A change to these points is already waiting for a second admin.'],
    ['override_unchanged', 'That wouldn’t change anything.'],
    ['points_out_of_range', 'Points must be between 0 and 50.'],
  ])('shows copy for %s', async (code, text) => {
    base({
      'GET /admin/scoring/deeds/deed-1': { body: makeDeed() },
      'POST /admin/scoring/deeds/deed-1/overrides': {
        status: code === 'points_out_of_range' ? 422 : 409,
        body: { detail: 'x', code },
      },
    })
    renderApp('/admin/scoring/deeds/deed-1')
    await fillForm('5')
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })

  it('checks points and note before sending', async () => {
    const api = base({ 'GET /admin/scoring/deeds/deed-1': { body: makeDeed() } })
    renderApp('/admin/scoring/deeds/deed-1')
    await fillForm('60')
    expect(await screen.findByRole('alert')).toHaveTextContent('Points must be between 0 and 50.')
    expect(api.calls('POST /admin/scoring/deeds/deed-1/overrides')).toHaveLength(0)
  })

  it('shows the final-month banner when the deed’s month is final', async () => {
    const api = base({
      'GET /admin/scoring/deeds/deed-1': { body: makeDeed({ month_final: true }) },
    })
    renderApp('/admin/scoring/deeds/deed-1')
    expect(await screen.findByText(FINAL_MONTH_TEXT)).toBeInTheDocument()
    // The month state comes with #9; the page no longer asks #10.
    expect(api.calls('GET /admin/scoring/months/2026-10')).toHaveLength(0)
  })

  it('shows no final-month banner while the month is open', async () => {
    base({ 'GET /admin/scoring/deeds/deed-1': { body: makeDeed() } })
    renderApp('/admin/scoring/deeds/deed-1')
    await screen.findByRole('heading', { name: 'Change the points' })
    expect(screen.queryByText(FINAL_MONTH_TEXT)).not.toBeInTheDocument()
  })

  it('shows NotFound on 404 (unknown or conflicted)', async () => {
    base({
      'GET /admin/scoring/deeds/deed-1': {
        status: 404,
        body: { detail: 'Not found', code: 'deed_not_found' },
      },
    })
    renderApp('/admin/scoring/deeds/deed-1')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })

  it('is NotFound for moderators', async () => {
    base({}, 'moderator')
    renderApp('/admin/scoring/deeds/deed-1')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})

describe('override requests page', () => {
  function page(items: OverrideRequest[], counts = { pending: items.length, decided: 2 }) {
    return { body: { items, counts, next_cursor: null } }
  }

  it('lists pending requests with old → new points and links', async () => {
    base({ 'GET /admin/scoring/override-requests': page([makeRequest()]) })
    renderApp('/admin/scoring/override-requests')
    expect(await screen.findByRole('tab', { name: 'Pending · 1' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Decided · 2' })).toBeInTheDocument()
    const row = (await screen.findByText('Points 12 → 0')).closest('article')!
    expect(row).toHaveTextContent('Ahmed Khan')
    expect(row).toHaveTextContent('Fake photo · asked by Ravi Admin')
    expect(row).toHaveTextContent('Photo is from the internet')
    expect(row).toHaveTextContent('Expires')
    expect(within(row).getByRole('link', { name: 'See the deed’s points' })).toHaveAttribute(
      'href',
      '/admin/scoring/deeds/deed-1',
    )
  })

  it('approves with a note', async () => {
    const api = base({
      'GET /admin/scoring/override-requests': [page([makeRequest()]), page([])],
      'POST /admin/scoring/override-requests/req-1/decision': {
        body: { request: makeRequest({ status: 'approved' }), deed: makeDeed() },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/scoring/override-requests')
    await user.click(await screen.findByRole('button', { name: /^Approve: Ahmed Khan/ }))
    const dialog = screen.getByRole('dialog', { name: 'Approve this cut?' })
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('at least 10 characters')
    await user.type(within(dialog).getByLabelText(/Note for the record/), 'Checked the photo')
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Approved.')
    expect(api.calls('POST /admin/scoring/override-requests/req-1/decision')[0].body).toEqual({
      decision: 'approve',
      note: 'Checked the photo',
    })
    expect(await screen.findByText('No requests waiting')).toBeInTheDocument()
  })

  it.each([
    [409, 'request_decided', 'Another admin already decided this request.'],
    [409, 'request_expired', 'This request has expired.'],
    [409, 'nothing_to_override', 'This report earns no points to change'],
    [404, 'request_not_found', 'This request isn’t available to you any more.'],
  ])('declining shows copy for %s %s', async (status, code, text) => {
    base({
      'GET /admin/scoring/override-requests': page([makeRequest()]),
      'POST /admin/scoring/override-requests/req-1/decision': {
        status,
        body: { detail: 'x', code },
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/scoring/override-requests')
    await user.click(await screen.findByRole('button', { name: /^Decline: Ahmed Khan/ }))
    const dialog = screen.getByRole('dialog', { name: 'Decline this cut?' })
    await user.type(within(dialog).getByLabelText(/Note for the record/), 'Not enough reason')
    await user.click(within(dialog).getByRole('button', { name: 'Decline' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(text)
  })

  it('shows decided requests with their outcome', async () => {
    const api = base({
      'GET /admin/scoring/override-requests': (req) =>
        req.query.get('status') === 'decided'
          ? page(
              [
                makeRequest({
                  status: 'declined',
                  decided_by: MOD,
                  decided_at: '2026-11-26T06:00:00Z',
                  decision_note: 'Not convinced',
                }),
              ],
              { pending: 0, decided: 1 },
            )
          : page([], { pending: 0, decided: 1 }),
    })
    const user = userEvent.setup()
    renderApp('/admin/scoring/override-requests')
    await user.click(await screen.findByRole('tab', { name: /Decided/ }))
    expect(await screen.findByText('Declined')).toBeInTheDocument()
    expect(screen.getByText('Note: Not convinced')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
    expect(api.requests.some((r) => r.query.get('status') === 'decided')).toBe(true)
  })
})

describe('user scoring admin page', () => {
  function makeUser(overrides: Partial<AdminUserScoring> = {}): AdminUserScoring {
    return {
      user: AHMED,
      titles: [
        {
          id: 't1',
          label: 'Kindness Hero · Koramangala · October 2026',
          period_month: '2026-10',
          region_name: 'Koramangala',
          level: 'ward',
          place: 1,
          revoked_at: null,
          revoked_reason: null,
        },
      ],
      badges: [
        {
          id: 'b1',
          slug: 'first_deed',
          label: 'First deed',
          awarded_at: '2026-10-02T06:00:00Z',
          revoked_at: '2026-11-20T06:00:00Z',
          revoked_reason: 'Confirmed ring',
        },
      ],
      doer_points: 140,
      reporter_points: 30,
      ...overrides,
    }
  }
  const CONFIRMED = {
    case_id: 'case-9',
    type: 'ring',
    status: 'resolved',
    outcome: 'confirmed',
    opened_at: '2026-10-20T06:00:00Z',
    resolved_at: '2026-10-25T06:00:00Z',
    summary: 'Ahmed, Priya and Ravi recognised each other 9 times',
  }
  const cases = (items: unknown[]) => ({
    body: { items, counts: { open: 0, parked: 0, resolved: items.length }, next_cursor: null },
  })

  it('disables Revoke without a confirmed case', async () => {
    const api = base({
      'GET /admin/scoring/users/ahmedk': { body: makeUser() },
      'GET /admin/fraud/cases': cases([{ ...CONFIRMED, outcome: 'dismissed' }]),
    })
    renderApp('/admin/users/ahmedk')
    expect(await screen.findByRole('heading', { name: 'Ahmed Khan' })).toBeInTheDocument()
    expect(
      await screen.findByText('Revoking needs a confirmed fraud case about this person.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Revoke Kindness Hero/ })).toBeDisabled()
    // Already revoked: no button, the revocation shows.
    expect(screen.queryByRole('button', { name: 'Revoke First deed' })).not.toBeInTheDocument()
    expect(screen.getByText(/Revoked .*: Confirmed ring/)).toBeInTheDocument()
    const query = api.calls('GET /admin/fraud/cases')[0].query
    expect(query.get('user')).toBe('ahmedk')
    expect(query.get('status')).toBe('resolved')
  })

  it('revokes a title with a confirmed case and a note', async () => {
    const api = base({
      'GET /admin/scoring/users/ahmedk': { body: makeUser() },
      'GET /admin/fraud/cases': cases([CONFIRMED]),
      'POST /admin/scoring/titles/t1/revoke': {
        body: makeUser({
          titles: [
            {
              ...makeUser().titles[0],
              revoked_at: '2026-11-26T06:00:00Z',
              revoked_reason: 'Ring confirmed',
            },
          ],
        }),
      },
    })
    const user = userEvent.setup()
    renderApp('/admin/users/ahmedk')
    const button = await screen.findByRole('button', { name: /^Revoke Kindness Hero/ })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText('Confirmed case')).toHaveDisplayValue(
      /Ahmed, Priya and Ravi recognised each other 9 times/,
    )
    await user.type(within(dialog).getByLabelText(/Note for the record/), 'Ring confirmed')
    await user.click(within(dialog).getByRole('button', { name: 'Revoke' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Revoked:')
    expect(api.calls('POST /admin/scoring/titles/t1/revoke')[0].body).toEqual({
      case_id: 'case-9',
      note: 'Ring confirmed',
    })
  })

  it.each([
    [
      'decision_not_final',
      'This decision can still be appealed. Revoking is possible once it is final.',
    ],
    ['case_not_confirmed', 'Revoking needs a confirmed fraud case about this person.'],
    ['already_revoked', 'This has already been revoked.'],
  ])('shows copy for %s', async (code, text) => {
    base({
      'GET /admin/scoring/users/ahmedk': { body: makeUser() },
      'GET /admin/fraud/cases': cases([CONFIRMED]),
      'POST /admin/scoring/titles/t1/revoke': { status: 409, body: { detail: 'x', code } },
    })
    const user = userEvent.setup()
    renderApp('/admin/users/ahmedk')
    const button = await screen.findByRole('button', { name: /^Revoke Kindness Hero/ })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Note for the record/), 'Ring confirmed')
    await user.click(within(dialog).getByRole('button', { name: 'Revoke' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(text)
  })

  it('shows NotFound on 404', async () => {
    base({
      'GET /admin/scoring/users/ahmedk': {
        status: 404,
        body: { detail: 'x', code: 'user_not_found' },
      },
    })
    renderApp('/admin/users/ahmedk')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})
