/**
 * QA4: My deeds (F6 canvas, M2-DESIGN §5.3 F6). Tabs, the Q20 note, pending deeds never naming
 * a doer, the withdraw dialog, edit sending only changed fields, and touch targets.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { AHMED, makeDeed, makeOptions } from '../report/testFixtures'
import type { DeedOut } from './deedsApi'

const ME = makeMe({ handle: 'priyas', display_name: 'Priya Sharma' })
const PRIYA = { handle: 'priyas', display_name: 'Priya Sharma' }
const RAHUL = { handle: 'rahulv', display_name: 'Rahul Verma' }
const Q20 =
  "Extra witness: 5 people already reported this act, so this report doesn't add to your count."

const reported = (overrides: Partial<DeedOut> = {}) =>
  makeDeed({ id: 'r1', reporter: PRIYA, doer: AHMED, ...overrides })
const about = (overrides: Partial<DeedOut> = {}) =>
  makeDeed({
    id: 'd1',
    reporter: RAHUL,
    doer: PRIYA,
    can_edit: false,
    can_withdraw: false,
    ...overrides,
  })
/** A pending invite deed: the API never names its doer (PRD §6.5). */
const pending = (overrides: Partial<DeedOut> = {}) =>
  reported({
    id: 'p1',
    status: 'pending',
    verification_method: 'invite_accept',
    doer: null,
    same_act: null,
    verified_at: null,
    ...overrides,
  })

/** The fetch mock drops the query string, so read `role` from the last fetch call. */
function lastRole(): string | null {
  const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
  const url = String(calls[calls.length - 1][0])
  return new URL(url, 'http://x').searchParams.get('role')
}

function setup(
  lists: { doer?: DeedOut[]; reporter?: DeedOut[] } = {},
  handlers: Parameters<typeof mockApi>[0] = {},
) {
  return mockApi({
    ...signedIn(ME),
    'GET /deeds/options': { body: makeOptions() },
    'GET /me/deeds': () => ({
      body: {
        items: (lastRole() === 'reporter' ? lists.reporter : lists.doer) ?? [],
        next_cursor: null,
      },
    }),
    ...handlers,
  })
}

describe('tabs', () => {
  it('falls back to Recognised for on an unknown ?tab and shows exactly two tabs', async () => {
    setup()
    renderApp('/me/deeds?tab=invites')
    const tablist = await screen.findByRole('tablist', { name: 'My deeds' })
    const tabs = within(tablist).getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Recognised for', 'I reported'])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(tabs[1]).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Recognised for')
    expect(screen.queryByRole('tab', { name: /invite/i })).not.toBeInTheDocument()
  })

  it('gives each tab a 44 px target and roving tabindex', async () => {
    setup()
    renderApp('/me/deeds?tab=reported')
    const tabs = await screen.findAllByRole('tab')
    for (const tab of tabs) expect(tab.className).toMatch(/\bmin-h-11\b/)
    expect(tabs[0]).toHaveAttribute('tabindex', '-1')
    expect(tabs[1]).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      'Deeds you saw others do. You can edit or withdraw your reports.',
    )
  })
})

describe('Recognised for (doer view)', () => {
  it('never shows Edit or Withdraw on reports about me, even if the API allowed them', async () => {
    setup({ doer: [about({ can_edit: true, can_withdraw: true })] })
    renderApp('/me/deeds')
    const card = (await screen.findByRole('link', { name: 'Rahul Verma recognised you' })).closest(
      'article',
    ) as HTMLElement
    expect(within(card).queryByRole('link', { name: /Edit/ })).not.toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /Withdraw/ })).not.toBeInTheDocument()
  })

  it('shows the Q20 note only on a non-counted report, word for word', async () => {
    setup({
      doer: [
        about({ id: 'a', same_act: null }),
        about({ id: 'b', same_act: { report_count: 3, counts_for_points: true } }),
        about({
          id: 'c',
          reporter: { handle: 'k', display_name: 'Kavya Nair' },
          same_act: { report_count: 6, counts_for_points: false },
        }),
      ],
    })
    renderApp('/me/deeds')
    await screen.findByRole('link', { name: 'Kavya Nair recognised you' })
    const notes = screen.getAllByText(/Extra witness/)
    expect(notes).toHaveLength(1)
    expect(notes[0]).toHaveTextContent(Q20)
    expect(
      screen.getByRole('link', { name: 'Kavya Nair recognised you' }).closest('article'),
    ).toContainElement(notes[0])
    // The old wording is gone (Q20 replaced it).
    expect(screen.queryByText(/The act counts once on your profile/)).not.toBeInTheDocument()
  })

  it('does not show the Q20 note to the reporter on the detail page', async () => {
    setup(
      {},
      {
        'GET /deeds/r1': {
          body: reported({ same_act: { report_count: 6, counts_for_points: false } }),
        },
      },
    )
    renderApp('/me/deeds/r1')
    await screen.findByRole('heading', { level: 1, name: 'You recognised Ahmed Khan' })
    expect(screen.queryByText(/Extra witness/)).not.toBeInTheDocument()
  })
})

describe('a pending deed never names a doer (PRD §6.5, §5.5)', () => {
  it('card and detail say "You recorded a deed"', async () => {
    setup({ reporter: [pending()] }, { 'GET /deeds/p1': { body: pending() } })
    const { router } = renderApp('/me/deeds?tab=reported')
    const link = await screen.findByRole('link', { name: 'You recorded a deed' })
    expect(within(link.closest('article') as HTMLElement).getByText('Pending')).toBeVisible()
    await router.navigate('/me/deeds/p1')
    expect(
      await screen.findByRole('heading', { level: 1, name: 'You recorded a deed' }),
    ).toBeVisible()
    expect(screen.getByText('Checked by').nextElementSibling).toHaveTextContent('Invite link')
  })

  it('the withdraw dialog for a pending deed names no one', async () => {
    setup({}, { 'GET /deeds/p1': { body: pending() } })
    const user = userEvent.setup()
    renderApp('/me/deeds/p1')
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }))
    expect(screen.getByRole('dialog')).toHaveAccessibleDescription(
      'This can’t be undone. The deed stops counting for them.',
    )
  })

  // BUG (Low, defence in depth): only `whoLine` checks the status. The withdraw dialog, the
  // withdrawn notice and the edit page's locked "Who" read `deed.doer` directly, so a pending
  // deed that carried a doer would be named there. §5.5: "Never render a doer on a pending
  // deed." The API sends `doer: null` for pending deeds today, so nothing leaks in practice.
  it('does not name a doer on a pending deed even if one were sent', async () => {
    const leaky = pending({ doer: AHMED })
    setup({}, { 'GET /deeds/p1': { body: leaky } })
    const user = userEvent.setup()
    const { router } = renderApp('/me/deeds/p1')
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }))
    expect(screen.getByRole('dialog')).not.toHaveTextContent('Ahmed')
    await user.keyboard('{Escape}')
    await router.navigate('/me/deeds/p1/edit')
    const locked = await screen.findByRole('region', { name: 'Can’t be changed' })
    expect(locked).not.toHaveTextContent('Ahmed')
  })
})

describe('withdraw dialog (F6)', () => {
  it('lists Withdraw report then Keep it, focuses Keep it, traps Tab, Esc returns focus', async () => {
    setup({}, { 'GET /deeds/r1': { body: reported() } })
    const user = userEvent.setup()
    renderApp('/me/deeds/r1')
    const opener = await screen.findByRole('button', { name: 'Withdraw' })
    await user.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Withdraw this report?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const buttons = within(dialog).getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['Withdraw report', 'Keep it'])
    expect(buttons[1]).toHaveFocus()
    await user.tab()
    expect(buttons[0]).toHaveFocus()
    await user.tab()
    expect(buttons[1]).toHaveFocus()
    await user.tab({ shift: true })
    expect(buttons[0]).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('Enter on the default choice keeps the report', async () => {
    const api = setup({}, { 'GET /deeds/r1': { body: reported() } })
    const user = userEvent.setup()
    renderApp('/me/deeds/r1')
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }))
    await user.keyboard('{Enter}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.calls('POST /deeds/r1/withdraw')).toHaveLength(0)
  })

  it('shows the withdrawn state from the server after withdrawing on the detail', async () => {
    let withdrawn = false
    const api = setup(
      {},
      {
        'GET /deeds/r1': () => ({
          body: withdrawn
            ? reported({
                status: 'withdrawn',
                withdrawn_at: '2026-11-16T10:00:00+05:30',
                can_edit: false,
                can_withdraw: false,
              })
            : reported(),
        }),
        'POST /deeds/r1/withdraw': () => {
          withdrawn = true
          return {
            body: reported({
              status: 'withdrawn',
              withdrawn_at: '2026-11-16T10:00:00+05:30',
              can_edit: false,
              can_withdraw: false,
            }),
          }
        },
      },
    )
    const user = userEvent.setup()
    renderApp('/me/deeds/r1')
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }))
    await user.click(screen.getByRole('button', { name: 'Withdraw report' }))
    expect(await screen.findByText(/You withdrew this report on 16 Nov\./)).toBeVisible()
    expect(api.calls('POST /deeds/r1/withdraw')).toHaveLength(1)
  })
})

describe('edit sends only changed fields; who, when and where stay locked', () => {
  it('offers no input for when, where or who', async () => {
    setup({}, { 'GET /deeds/r1': { body: reported() } })
    renderApp('/me/deeds/r1/edit')
    await screen.findByRole('heading', { name: 'Edit your report' })
    const form = document.querySelector('main form') as HTMLFormElement
    expect(within(form).queryAllByRole('textbox')).toHaveLength(1)
    expect(form.querySelector('input[type="date"], input[type="time"], select')).toBeNull()
    expect(within(form).queryByRole('button', { name: /location|Earlier|Just now/i })).toBeNull()
  })

  it('treats a whitespace-only change as no change', async () => {
    const api = setup({}, { 'GET /deeds/r1': { body: reported() } })
    const user = userEvent.setup()
    const { router } = renderApp('/me/deeds/r1/edit')
    const description = await screen.findByRole('textbox', { name: 'What did they do?' })
    await user.type(description, '   ')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/deeds/r1'))
    expect(api.calls('PATCH /deeds/r1')).toHaveLength(0)
  })

  it('leaves a stored "not given" people count alone when only the category changes', async () => {
    const api = setup(
      {},
      {
        'GET /deeds/r1': { body: reported({ beneficiaries_count: null }) },
        'PATCH /deeds/r1': { body: reported({ category: 'other', beneficiaries_count: null }) },
      },
    )
    const user = userEvent.setup()
    renderApp('/me/deeds/r1/edit')
    await user.click(await screen.findByRole('button', { name: 'Other' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.calls('PATCH /deeds/r1')).toHaveLength(1))
    expect(api.calls('PATCH /deeds/r1')[0].body).toEqual({ category: 'other' })
  })

  it('sends a new people count alone, never occurred_at, location or doer', async () => {
    const api = setup(
      {},
      {
        'GET /deeds/r1': { body: reported({ beneficiaries_count: 2 }) },
        'PATCH /deeds/r1': { body: reported({ beneficiaries_count: 3 }) },
      },
    )
    const user = userEvent.setup()
    renderApp('/me/deeds/r1/edit')
    await user.click(await screen.findByRole('button', { name: 'One more' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.calls('PATCH /deeds/r1')).toHaveLength(1))
    const body = api.calls('PATCH /deeds/r1')[0].body as Record<string, unknown>
    expect(body).toEqual({ beneficiaries_count: 3 })
  })

  it('shows "This deed isn’t available." for a deed I can’t see', async () => {
    setup({}, { 'GET /deeds/zz': { status: 404, body: { detail: 'x', code: 'deed_not_found' } } })
    renderApp('/me/deeds/zz/edit')
    expect(await screen.findByRole('alert')).toHaveTextContent('This deed isn’t available.')
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument()
  })
})

describe('touch targets (§5.5: ≥ 44 px)', () => {
  it('gives Edit and Withdraw on a card 44 px', async () => {
    setup({ reporter: [reported()] })
    renderApp('/me/deeds?tab=reported')
    const card = (await screen.findByRole('link', { name: 'You recognised Ahmed Khan' })).closest(
      'article',
    ) as HTMLElement
    expect(within(card).getByRole('link', { name: /^Edit:/ }).className).toMatch(/\bmin-h-11\b/)
    expect(within(card).getByRole('button', { name: /^Withdraw:/ }).className).toMatch(
      /\bmin-h-11\b/,
    )
  })

  // BUG (Low): the who line on each card, which is the link that opens the deed, is
  // `min-h-10` (40 px), under the 44 px minimum in §5.5.
  it('gives the card’s who-line link (opens the detail) at least 44 px', async () => {
    setup({ reporter: [reported()] })
    renderApp('/me/deeds?tab=reported')
    const link = await screen.findByRole('link', { name: 'You recognised Ahmed Khan' })
    expect(link.className).toMatch(/\bmin-h-1[1-3]\b/)
  })
})
