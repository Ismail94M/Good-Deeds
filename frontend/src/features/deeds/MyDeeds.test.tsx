/** F6a: My deeds tabs and cards, deed detail, withdraw and edit. */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'
import { AHMED, makeDeed, makeOptions } from '../report/testFixtures'
import type { DeedOut } from './deedsApi'

const ME = makeMe({ handle: 'priyas', display_name: 'Priya Sharma' })
const PRIYA = { handle: 'priyas', display_name: 'Priya Sharma' }
const RAHUL = { handle: 'rahulv', display_name: 'Rahul Verma' }

/** A report Priya made about Ahmed. */
const reported = (overrides: Partial<DeedOut> = {}) =>
  makeDeed({ id: 'r1', reporter: PRIYA, doer: AHMED, ...overrides })
/** A report Rahul made about Priya. */
const about = (overrides: Partial<DeedOut> = {}) =>
  makeDeed({
    id: 'd1',
    reporter: RAHUL,
    doer: PRIYA,
    category: 'animals',
    description: 'Fed and gave water to stray puppies.',
    can_edit: false,
    can_withdraw: false,
    ...overrides,
  })

const page = (items: DeedOut[], next_cursor: string | null = null) => ({
  body: { items, next_cursor },
})

function setup(handlers: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    ...signedIn(ME),
    'GET /deeds/options': { body: makeOptions() },
    'GET /me/deeds': page([]),
    ...handlers,
  })
}

/** The mock keys by path only, so tell the roles apart from the fetch URL. */
function deedsByRole(doer: DeedOut[] | Error, reporter: DeedOut[] | Error) {
  return {
    'GET /me/deeds': () => {
      const url = String(lastFetchUrl())
      const role = new URL(url, 'http://x').searchParams.get('role')
      const items = role === 'reporter' ? reporter : doer
      if (items instanceof Error) return { status: 500, body: { detail: items.message } }
      return page(items)
    },
  }
}

let fetchSpy: { mock: { calls: unknown[][] } } | null = null
function lastFetchUrl(): string {
  const calls = fetchSpy?.mock.calls ?? []
  const input = calls[calls.length - 1]?.[0]
  return typeof input === 'string' ? input : input instanceof URL ? input.href : ''
}
function track(api: ReturnType<typeof mockApi>) {
  fetchSpy = api.spy as unknown as { mock: { calls: unknown[][] } }
  return api
}

describe('My deeds tabs', () => {
  it('has exactly two tabs and opens on Recognised for', async () => {
    track(setup(deedsByRole([about()], [reported()])))
    renderApp('/me/deeds')

    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Recognised for', 'I reported'])
    expect(screen.getByRole('tab', { name: 'Recognised for' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByText('Deeds other people saw you do.')).toBeVisible()
    expect(await screen.findByRole('link', { name: 'Rahul Verma recognised you' })).toHaveAttribute(
      'href',
      '/me/deeds/d1',
    )
    expect(screen.queryByText(/invite/i)).not.toBeInTheDocument()
  })

  it('opens I reported from ?tab=reported (as F3 links to it)', async () => {
    track(setup(deedsByRole([about()], [reported()])))
    renderApp('/me/deeds?tab=reported')
    expect(await screen.findByRole('tab', { name: 'I reported' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(
      screen.getByText('Deeds you saw others do. You can edit or withdraw your reports.'),
    ).toBeVisible()
    expect(await screen.findByRole('link', { name: 'You recognised Ahmed Khan' })).toBeVisible()
  })

  it('switches tabs with clicks and arrow keys, keeping the URL in step', async () => {
    track(setup(deedsByRole([about()], [reported()])))
    const user = userEvent.setup()
    const { router } = renderApp('/me/deeds')
    await user.click(await screen.findByRole('tab', { name: 'I reported' }))
    expect(router.state.location.search).toBe('?tab=reported')
    expect(await screen.findByRole('link', { name: 'You recognised Ahmed Khan' })).toBeVisible()

    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('tab', { name: 'Recognised for' })).toHaveFocus()
    expect(router.state.location.search).toBe('')
  })
})

describe('deed cards', () => {
  it('shows badge, date, Edited, category, description and no photos', async () => {
    track(
      setup(
        deedsByRole(
          [about({ last_edited_at: '2026-11-17T10:00:00Z', occurred_at: '2026-11-14T10:00:00Z' })],
          [],
        ),
      ),
    )
    renderApp('/me/deeds')
    const card = (await screen.findByRole('link', { name: 'Rahul Verma recognised you' })).closest(
      'article',
    ) as HTMLElement
    expect(within(card).getByText('Verified')).toBeVisible()
    expect(within(card).getByText('14 Nov · Edited')).toBeVisible()
    expect(within(card).getByText('Animals')).toBeVisible()
    expect(within(card).getByText('Fed and gave water to stray puppies.')).toHaveClass(
      'line-clamp-3',
    )
    expect(within(card).queryByText(/photo/i)).not.toBeInTheDocument()
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
  })

  it('notes extra witnesses on Recognised for only when the report doesn’t count (Q20)', async () => {
    track(
      setup(
        deedsByRole(
          [
            about({ id: 'counted' }),
            about({
              id: 'extra',
              reporter: { handle: 'sneha', display_name: 'Sneha Rao' },
              same_act: { report_count: 6, counts_for_points: false },
            }),
          ],
          [],
        ),
      ),
    )
    renderApp('/me/deeds')
    const note =
      "Extra witness: 5 people already reported this act, so this report doesn't add to your count."
    expect(await screen.findByText(note)).toBeVisible()
    expect(screen.getAllByText(note)).toHaveLength(1)
    const counted = screen
      .getByRole('link', { name: 'Rahul Verma recognised you' })
      .closest('article') as HTMLElement
    expect(within(counted).queryByText(/Extra witness/)).not.toBeInTheDocument()
  })

  it('takes the cap from options when the API sends it', async () => {
    track(
      setup({
        'GET /deeds/options': { body: { ...makeOptions(), same_act_cap: 7 } },
        ...deedsByRole([about({ same_act: { report_count: 9, counts_for_points: false } })], []),
      }),
    )
    renderApp('/me/deeds')
    expect(await screen.findByText(/^Extra witness: 7 people/)).toBeVisible()
  })

  it('shows Edit and Withdraw on my reports only when allowed', async () => {
    track(
      setup(
        deedsByRole(
          [],
          [
            reported(),
            reported({
              id: 'r2',
              status: 'withdrawn',
              doer: { handle: 'arjun', display_name: 'Arjun Mehta' },
              can_edit: false,
              can_withdraw: false,
            }),
          ],
        ),
      ),
    )
    renderApp('/me/deeds?tab=reported')
    const live = (await screen.findByRole('link', { name: 'You recognised Ahmed Khan' })).closest(
      'article',
    ) as HTMLElement
    expect(
      within(live).getByRole('link', { name: 'Edit: You recognised Ahmed Khan' }),
    ).toHaveAttribute('href', '/me/deeds/r1/edit')
    expect(
      within(live).getByRole('button', { name: 'Withdraw: You recognised Ahmed Khan' }),
    ).toBeVisible()

    const gone = screen
      .getByRole('link', { name: 'You recognised Arjun Mehta' })
      .closest('article') as HTMLElement
    expect(within(gone).getByText('Withdrawn')).toBeVisible()
    expect(within(gone).queryByText('Edit')).not.toBeInTheDocument()
    expect(within(gone).queryByText('Withdraw')).not.toBeInTheDocument()
    // The reporter doesn't see the extra-witness note.
    expect(screen.queryByText(/Extra witness/)).not.toBeInTheDocument()
  })

  it('withdraws from the list after confirming', async () => {
    const api = track(
      setup({
        ...deedsByRole([], [reported()]),
        'POST /deeds/r1/withdraw': {
          body: reported({ status: 'withdrawn', can_edit: false, can_withdraw: false }),
        },
      }),
    )
    const user = userEvent.setup()
    renderApp('/me/deeds?tab=reported')
    await user.click(
      await screen.findByRole('button', { name: 'Withdraw: You recognised Ahmed Khan' }),
    )
    const dialog = screen.getByRole('dialog', { name: 'Withdraw this report?' })
    expect(dialog).toHaveAccessibleDescription(
      'This can’t be undone. The deed stops counting for Ahmed Khan.',
    )
    expect(within(dialog).getByRole('button', { name: 'Keep it' })).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Keep it' }))
    expect(api.calls('POST /deeds/r1/withdraw')).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Withdraw: You recognised Ahmed Khan' }))
    await user.click(screen.getByRole('button', { name: 'Withdraw report' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.calls('POST /deeds/r1/withdraw')).toHaveLength(1)
  })

  it('pages with Show more', async () => {
    let call = 0
    const api = setup({
      'GET /me/deeds': () => {
        call += 1
        return call === 1
          ? page([about({ id: 'a' })], 'cursor-2')
          : page([about({ id: 'b', reporter: { handle: 'k', display_name: 'Kavya Nair' } })])
      },
    })
    const user = userEvent.setup()
    renderApp('/me/deeds')
    await user.click(await screen.findByRole('button', { name: 'Show more' }))
    expect(await screen.findByRole('link', { name: 'Kavya Nair recognised you' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
    expect(String(api.spy.mock.calls.at(-1)?.[0])).toContain('cursor=cursor-2')
  })
})

describe('empty and error states', () => {
  it('Recognised for: show your QR', async () => {
    track(setup(deedsByRole([], [])))
    renderApp('/me/deeds')
    expect(
      await screen.findByRole('heading', { name: 'No one has recognised you yet' }),
    ).toBeVisible()
    expect(
      screen.getByText('Next time you help someone, show your QR so a witness can record it.'),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Show my QR' })).toHaveAttribute('href', '/me/qr')
  })

  it('I reported: record a deed', async () => {
    track(setup(deedsByRole([], [])))
    renderApp('/me/deeds?tab=reported')
    expect(
      await screen.findByRole('heading', { name: 'You haven’t recorded a deed yet' }),
    ).toBeVisible()
    const main = screen.getByRole('main')
    expect(within(main).getByRole('link', { name: 'Record a deed' })).toHaveAttribute(
      'href',
      '/report',
    )
  })

  it('shows an error with Try again', async () => {
    track(setup(deedsByRole(new Error('Something went wrong. Please try again.'), [])))
    renderApp('/me/deeds')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong.')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
  })

  it('works before onboarding, without options (category falls back to the slug)', async () => {
    track(
      mockApi({
        ...signedIn(makeMe({ handle: 'priyas', phone_e164: null, onboarding: NOT_ONBOARDED })),
        'GET /deeds/options': { status: 403, body: { detail: 'x', code: 'onboarding_required' } },
        ...deedsByRole([about({ category: 'road_safety' })], []),
      }),
    )
    renderApp('/me/deeds')
    expect(await screen.findByText('Road safety')).toBeVisible()
  })
})

describe('deed detail', () => {
  it('shows my report with Edit and Withdraw, and no feed row', async () => {
    setup({
      'GET /deeds/r1': {
        body: reported({
          beneficiaries_count: 2,
          last_edited_at: '2026-11-17T10:00:00Z',
        }),
      },
    })
    renderApp('/me/deeds/r1')
    expect(
      await screen.findByRole('heading', { level: 1, name: 'You recognised Ahmed Khan' }),
    ).toBeVisible()
    expect(screen.getByText('Verified')).toBeVisible()
    expect(screen.getByText('Edited')).toBeVisible()
    expect(screen.getByText('Helping an elderly person')).toBeVisible()
    const facts = document.querySelector('main dl') as HTMLElement
    expect(facts).toHaveTextContent('People helped2')
    expect(facts).toHaveTextContent('Checked byQR code and location')
    expect(facts).toHaveTextContent('Last edited')
    expect(screen.getByRole('link', { name: 'Edit' })).toHaveAttribute('href', '/me/deeds/r1/edit')
    expect(screen.getByRole('button', { name: 'Withdraw' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'My deeds' })).toHaveAttribute(
      'href',
      '/me/deeds?tab=reported',
    )
    expect(screen.queryByText(/feed/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/photo/i)).not.toBeInTheDocument()
  })

  it('hides People helped when not given', async () => {
    setup({ 'GET /deeds/r1': { body: reported({ beneficiaries_count: null }) } })
    renderApp('/me/deeds/r1')
    await screen.findByRole('heading', { level: 1, name: 'You recognised Ahmed Khan' })
    expect(screen.queryByText('People helped')).not.toBeInTheDocument()
  })

  it('shows a report about me as "{reporter} recognised you", without actions', async () => {
    setup({
      'GET /deeds/d1': { body: about({ same_act: { report_count: 6, counts_for_points: false } }) },
    })
    renderApp('/me/deeds/d1')
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Rahul Verma recognised you' }),
    ).toBeVisible()
    expect(screen.getByText(/^Extra witness: 5 people/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Withdraw' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Edit' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'My deeds' })).toHaveAttribute('href', '/me/deeds')
  })

  it('withdraws after confirming and shows the withdrawn notice', async () => {
    const withdrawn = reported({
      status: 'withdrawn',
      withdrawn_at: '2026-11-16T10:00:00+05:30',
      can_edit: false,
      can_withdraw: false,
    })
    const api = setup({
      'GET /deeds/r1': [{ body: reported() }, { body: withdrawn }],
      'POST /deeds/r1/withdraw': { body: withdrawn },
    })
    const user = userEvent.setup()
    renderApp('/me/deeds/r1')
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }))
    await user.click(screen.getByRole('button', { name: 'Withdraw report' }))

    expect(
      await screen.findByText(
        'You withdrew this report on 16 Nov. It no longer counts for Ahmed Khan.',
      ),
    ).toBeVisible()
    expect(screen.getByText('Withdrawn')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Withdraw' })).not.toBeInTheDocument()
    expect(api.calls('POST /deeds/r1/withdraw')).toHaveLength(1)
  })

  it('shows the error inside the dialog when withdrawing fails', async () => {
    setup({
      'GET /deeds/r1': { body: reported() },
      'POST /deeds/r1/withdraw': {
        status: 409,
        body: { detail: 'This report can no longer be withdrawn.', code: 'deed_not_withdrawable' },
      },
    })
    const user = userEvent.setup()
    renderApp('/me/deeds/r1')
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }))
    await user.click(screen.getByRole('button', { name: 'Withdraw report' }))
    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'This report can no longer be withdrawn.',
    )
  })

  it('shows "This deed isn’t available" on a 404', async () => {
    setup({
      'GET /deeds/zzz': { status: 404, body: { detail: 'Not found', code: 'deed_not_found' } },
    })
    renderApp('/me/deeds/zzz')
    expect(await screen.findByRole('heading', { name: 'This deed isn’t available' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to My deeds' })).toHaveAttribute(
      'href',
      '/me/deeds',
    )
  })
})

describe('edit', () => {
  it('shows who, when and where locked, and sends only the changed fields', async () => {
    const api = setup({
      'GET /deeds/r1': { body: reported({ beneficiaries_count: 1 }) },
      'PATCH /deeds/r1': { body: reported({ description: 'Helped an old man cross Silk Board.' }) },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/deeds/r1/edit')

    expect(await screen.findByRole('heading', { name: 'Edit your report' })).toBeVisible()
    const locked = screen.getByRole('region', { name: 'Can’t be changed' })
    expect(locked).toHaveTextContent('WhoAhmed Khan')
    expect(locked).toHaveTextContent(/When(Today|Yesterday|\d+ \w+), /)
    expect(locked).toHaveTextContent('WhereChecked by QR and location')
    expect(locked).toHaveTextContent('These are the proof that you were there.')
    expect(screen.queryByText(/photo/i)).not.toBeInTheDocument()
    expect(
      screen.getByText('Changes are saved with a history, and the deed shows “Edited”.'),
    ).toBeVisible()

    const description = screen.getByRole('textbox', { name: 'What did they do?' })
    await user.clear(description)
    await user.type(description, 'Helped an old man cross Silk Board.')
    await user.click(screen.getByRole('button', { name: 'One fewer' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/me/deeds/r1'))
    expect(api.calls('PATCH /deeds/r1')[0].body).toEqual({
      description: 'Helped an old man cross Silk Board.',
      beneficiaries_count: null,
    })
  })

  it('changes the category alone', async () => {
    const api = setup({
      'GET /deeds/r1': { body: reported() },
      'PATCH /deeds/r1': { body: reported({ category: 'animals' }) },
    })
    const user = userEvent.setup()
    renderApp('/me/deeds/r1/edit')
    expect(
      await screen.findByRole('button', { name: 'Helping an elderly person' }),
    ).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Animals' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.calls('PATCH /deeds/r1')).toHaveLength(1))
    expect(api.calls('PATCH /deeds/r1')[0].body).toEqual({ category: 'animals' })
  })

  it('sends nothing when nothing changed', async () => {
    const api = setup({ 'GET /deeds/r1': { body: reported() } })
    const user = userEvent.setup()
    const { router } = renderApp('/me/deeds/r1/edit')
    await user.click(await screen.findByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/deeds/r1'))
    expect(api.calls('PATCH /deeds/r1')).toHaveLength(0)
  })

  it('needs a long enough description', async () => {
    const api = setup({ 'GET /deeds/r1': { body: reported() } })
    const user = userEvent.setup()
    renderApp('/me/deeds/r1/edit')
    const description = await screen.findByRole('textbox', { name: 'What did they do?' })
    await user.clear(description)
    await user.type(description, 'Short')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(
      screen.getByText('Write at least 10 characters so people understand what happened.'),
    ).toBeVisible()
    expect(api.calls('PATCH /deeds/r1')).toHaveLength(0)
  })

  it('goes back to the detail with a notice on 409 deed_not_editable', async () => {
    setup({
      'GET /deeds/r1': { body: reported() },
      'PATCH /deeds/r1': {
        status: 409,
        body: { detail: 'Not editable.', code: 'deed_not_editable' },
      },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/me/deeds/r1/edit')
    await user.click(await screen.findByRole('button', { name: 'Animals' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/me/deeds/r1'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This report can’t be edited any more.',
    )
  })

  it('refuses to edit a report that can’t be edited', async () => {
    setup({ 'GET /deeds/r1': { body: reported({ status: 'withdrawn', can_edit: false }) } })
    renderApp('/me/deeds/r1/edit')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This report can’t be edited any more.',
    )
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument()
  })
})
