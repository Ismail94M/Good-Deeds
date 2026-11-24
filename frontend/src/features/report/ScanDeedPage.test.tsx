/** F3b: the deed form on `/report/scan/:scanId`, its results and its errors. */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn, type Reply } from '../../test/utils'
import type { DeedOptions, ScanDetail } from '../deeds/deedsApi'
import { AHMED, makeDeed, makeOptions, makeScanDetail, SCAN_ID } from './testFixtures'

const PATH = `/report/scan/${SCAN_ID}`

function setup({
  scan = makeScanDetail(),
  scanReply,
  options = makeOptions(),
  deedReply = { status: 201, body: makeDeed() },
}: {
  scan?: ScanDetail
  scanReply?: Reply
  options?: DeedOptions
  deedReply?: Reply | Reply[]
} = {}) {
  return mockApi({
    ...signedIn(makeMe({ display_name: 'Priya Sharma' })),
    'GET /deeds/options': { body: options },
    [`GET /scans/${SCAN_ID}`]: scanReply ?? { body: scan },
    'POST /deeds': deedReply,
  })
}

async function fillValid(user = userEvent.setup()) {
  await user.click(await screen.findByRole('button', { name: 'Helping an elderly person' }))
  await user.type(
    screen.getByRole('textbox', { name: 'What did they do?' }),
    'Helped an elderly man cross the road.',
  )
  return user
}

describe('deed form', () => {
  it('loads the scan (so it survives a reload) and shows who, the countdown and the fields', async () => {
    const api = setup()
    renderApp(PATH)

    const who = await screen.findByRole('region', { name: 'Who you’re recognising' })
    expect(within(who).getByText('Recognising')).toBeVisible()
    expect(within(who).getByText('Ahmed Khan')).toBeVisible()
    expect(within(who).getByText('@ahmedk')).toBeVisible()
    expect(within(who).getByText('AK')).toBeInTheDocument()
    expect(within(who).getByText('Matched')).toBeVisible()
    expect(screen.getByRole('timer')).toHaveTextContent(
      /Send within 1[45]:\d\d\. A scan stays valid for 15 minutes\./,
    )
    expect(api.calls(`GET /scans/${SCAN_ID}`)).toHaveLength(1)

    expect(screen.getByRole('group', { name: 'What kind of deed?' })).toBeVisible()
    const description = screen.getByRole('textbox', { name: 'What did they do?' })
    expect(description).toHaveAttribute('rows', '3')
    expect(
      screen.getByText('At least 10 characters. Please leave out phone numbers and addresses.'),
    ).toBeVisible()
    expect(screen.getByText('0 / 1000')).toBeVisible()
    expect(screen.getByRole('group', { name: /How many people did they help\?/ })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Just now' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Up to 2 hours before you scanned.')).toBeVisible()
    expect(screen.getByText('It goes on Ahmed’s profile as soon as you send it.')).toBeVisible()
  })

  it('renders no photo controls while photos are off', async () => {
    setup()
    renderApp(PATH)
    await screen.findByText('Recognising')
    expect(screen.queryByText(/photo/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Take photo|Upload/ })).not.toBeInTheDocument()
  })

  it('shows the public-feed notice, naming the doer, directly above Send', async () => {
    setup()
    renderApp(PATH)
    const notice = await screen.findByText('Verified deeds appear in the public feed.')
    const box = notice.closest('div') as HTMLElement
    expect(box).toHaveTextContent(
      'Verified deeds appear in the public feed. You or Ahmed can hide it at any time. Only the area name is shown, never your exact location.',
    )
    const send = screen.getByRole('button', { name: 'Send report' })
    expect(box.compareDocumentPosition(send) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(box.nextElementSibling).toContainElement(send)
  })

  it.each([
    [3, 'You can record Ahmed 3 more times this month. Resets on 1 December.'],
    [1, 'You can record Ahmed 1 more time this month. Resets on 1 December.'],
  ])('warns at %i left for this doer', async (left, text) => {
    setup({ scan: makeScanDetail({ pair_reports_remaining: left }) })
    renderApp(PATH)
    await screen.findByText('Recognising')
    const lead = text.slice(0, text.indexOf('.') + 1)
    const banner = screen.getByText(lead).closest('[role="status"]')
    expect(banner).toHaveTextContent(text)
  })

  it('does not warn at 4 left', async () => {
    setup({ scan: makeScanDetail({ pair_reports_remaining: 4 }) })
    renderApp(PATH)
    await screen.findByText('Recognising')
    expect(screen.queryByText(/more times? this month/)).not.toBeInTheDocument()
  })

  it('takes the pair warning threshold from the deed options (config)', async () => {
    setup({
      scan: makeScanDetail({ pair_reports_remaining: 5 }),
      options: makeOptions({ reporter_doer_monthly_warn_at: 5 }),
    })
    renderApp(PATH)
    await screen.findByText('Recognising')
    expect(screen.getByText('You can record Ahmed 5 more times this month.')).toBeVisible()
  })

  it('needs a category and a long enough description before sending', async () => {
    const api = setup()
    const user = userEvent.setup()
    renderApp(PATH)
    await user.type(await screen.findByRole('textbox', { name: 'What did they do?' }), 'Helped')
    expect(screen.getByText('6 / 1000')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Send report' }))

    expect(screen.getByText('Choose what kind of deed it was.')).toBeVisible()
    expect(
      screen.getByText('Write at least 10 characters so people understand what happened.'),
    ).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'What did they do?' })).toHaveAttribute(
      'aria-invalid',
      'true',
    )
    expect(api.calls('POST /deeds')).toHaveLength(0)
  })

  it('sends Just now without occurred_at, and shows "Deed verified"', async () => {
    const api = setup()
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'One more' }))
    await user.click(screen.getByRole('button', { name: 'Send report' }))

    expect(await screen.findByRole('heading', { name: 'Deed verified' })).toHaveFocus()
    expect(screen.getByText('Thanks, Priya. It now counts on Ahmed Khan’s profile.')).toBeVisible()
    expect(api.calls('POST /deeds')[0].body).toEqual({
      method: 'qr_scan',
      scan_id: SCAN_ID,
      category: 'helping_elderly',
      description: 'Helped an elderly man cross the road.',
      beneficiaries_count: 2,
    })
    const summary = document.querySelector('main dl') as HTMLElement
    expect(summary).toHaveTextContent('WhoAhmed Khan')
    expect(summary).toHaveTextContent('DeedHelping an elderly person')
    expect(summary).toHaveTextContent(/WhenToday, \d{1,2}:\d\d [ap]m/)
    expect(summary).toHaveTextContent('Checked byQR code and location')
    expect(summary).not.toHaveTextContent('Photos')
    expect(screen.getByRole('link', { name: 'Record another deed' })).toHaveAttribute(
      'href',
      '/report',
    )
    expect(screen.getByRole('link', { name: 'See my reports' })).toHaveAttribute(
      'href',
      '/me/deeds?tab=reported',
    )
  })

  it('sends Earlier as scanned_at minus the chosen offset, and 0 people as null', async () => {
    const scan = makeScanDetail({ scanned_at: '2026-11-17T10:00:00.000Z' })
    const api = setup({ scan })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'One fewer' }))
    await user.click(screen.getByRole('button', { name: 'Earlier' }))
    const select = screen.getByRole('combobox', { name: 'How long before you scanned?' })
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['About 15 minutes', 'About 30 minutes', 'About 1 hour', 'About 2 hours'])
    await user.selectOptions(select, 'About 30 minutes')
    await user.click(screen.getByRole('button', { name: 'Send report' }))

    await screen.findByRole('heading', { name: 'Deed verified' })
    expect(api.calls('POST /deeds')[0].body).toMatchObject({
      occurred_at: '2026-11-17T09:30:00.000Z',
      beneficiaries_count: null,
    })
  })

  it('hides Earlier choices beyond the configured maximum', async () => {
    setup({ options: makeOptions({ qr_occurred_max_age_minutes: 60 }) })
    const user = userEvent.setup()
    renderApp(PATH)
    await user.click(await screen.findByRole('button', { name: 'Earlier' }))
    const select = screen.getByRole('combobox', { name: 'How long before you scanned?' })
    expect(within(select).queryByRole('option', { name: 'About 2 hours' })).toBeNull()
    expect(screen.getByText('Up to 1 hour before you scanned.')).toBeVisible()
  })

  it('shows "Saved as an extra witness" when the report doesn’t count', async () => {
    setup({
      deedReply: {
        status: 201,
        body: makeDeed({ same_act: { report_count: 6, counts_for_points: false } }),
      },
    })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Saved as an extra witness' })).toBeVisible()
    expect(
      screen.getByText(
        'Others already reported this act. Your report backs theirs up, but it doesn’t count a second time on Ahmed’s profile.',
      ),
    ).toBeVisible()
  })

  it('shows API validation errors inline and keeps the form', async () => {
    setup({
      deedReply: {
        status: 400,
        body: { detail: 'That time is outside the allowed range.', code: 'implausible_time' },
      },
    })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That time is outside the allowed range.',
    )
    expect(screen.getByRole('button', { name: 'Send report' })).toBeVisible()
  })

  it('keeps what was typed after a network error and Try again', async () => {
    const api = setup({
      deedReply: [
        { status: 503, body: { detail: 'down' } },
        { status: 201, body: makeDeed() },
      ],
    })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Something went wrong' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByRole('textbox', { name: 'What did they do?' })).toHaveValue(
      'Helped an elderly man cross the road.',
    )
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Deed verified' })).toBeVisible()
    expect(api.calls('POST /deeds')).toHaveLength(2)
  })
})

describe('deed-time errors', () => {
  const error = (status: number, code: string) => ({
    status,
    body: { detail: `detail for ${code}`, code },
  })

  it.each([
    [
      error(410, 'scan_expired'),
      'Time ran out for this report',
      "Reports must be sent within 15 minutes of scanning. Scan Ahmed's QR again to start over.",
      'Scan again',
      '/report/scan',
    ],
    [
      error(409, 'scan_used'),
      'This report was already sent',
      "You'll find it in My deeds.",
      'See my reports',
      '/me/deeds?tab=reported',
    ],
    [
      error(409, 'already_recognized_today'),
      "You've already recognised Ahmed today",
      'You can recognise the same person once a day. You can recognise Ahmed again tomorrow, or record someone else now.',
      'Record someone else',
      '/report',
    ],
    [
      error(429, 'pair_monthly_limit'),
      "You've recorded Ahmed 15 times this month",
      "That's the most for one person. You can record Ahmed again from 1 December, and you can still record anyone else.",
      'Record someone else',
      '/report',
    ],
    [
      error(429, 'monthly_report_limit'),
      "You've recorded 150 deeds this month",
      "That's the monthly maximum. You can record more from 1 December.",
      'Back to Home',
      '/',
    ],
  ])('%#: %s', async (reply, title, body, action, href) => {
    setup({ deedReply: reply })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: title })).toBeVisible()
    expect(screen.getByText(body)).toBeVisible()
    expect(screen.getByRole('link', { name: action })).toHaveAttribute('href', href)
  })
})

describe('loading the scan', () => {
  it('shows scan_used for a scan that already has a deed', async () => {
    setup({ scan: makeScanDetail({ consumed: true }) })
    renderApp(PATH)
    expect(
      await screen.findByRole('heading', { name: 'This report was already sent' }),
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Send report' })).not.toBeInTheDocument()
  })

  it('shows scan_expired on a 410', async () => {
    setup({ scanReply: { status: 410, body: { detail: 'Expired.', code: 'scan_expired' } } })
    renderApp(PATH)
    expect(
      await screen.findByRole('heading', { name: 'Time ran out for this report' }),
    ).toBeVisible()
    expect(
      screen.getByText(
        'Reports must be sent within 15 minutes of scanning. Scan their QR again to start over.',
      ),
    ).toBeVisible()
  })

  it('shows scan_not_found on a 404', async () => {
    setup({ scanReply: { status: 404, body: { detail: 'Not found.', code: 'scan_not_found' } } })
    renderApp(PATH)
    expect(
      await screen.findByRole('heading', { name: "This report link isn't valid" }),
    ).toBeVisible()
    expect(
      within(screen.getByRole('main')).getByRole('link', { name: 'Record a deed' }),
    ).toHaveAttribute('href', '/report')
  })

  it('replaces the form with scan_expired when the countdown reaches 0', async () => {
    setup({
      scan: makeScanDetail({ expires_at: new Date(Date.now() + 1500).toISOString() }),
    })
    renderApp(PATH)
    expect(await screen.findByRole('timer')).toHaveTextContent(/Send within 0:0[12]\./)
    expect(
      await screen.findByRole(
        'heading',
        { name: 'Time ran out for this report' },
        { timeout: 4000 },
      ),
    ).toBeVisible()
    expect(screen.getByText(/Scan Ahmed's QR again to start over\./)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Send report' })).not.toBeInTheDocument()
  })
})

describe('Cancel', () => {
  it('asks first, with Keep writing as the default', async () => {
    setup()
    const user = userEvent.setup()
    const { router } = renderApp(PATH)
    await user.type(await screen.findByRole('textbox', { name: 'What did they do?' }), 'Draft')
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    await user.click(cancel)

    const dialog = screen.getByRole('dialog', { name: 'Cancel this report?' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleDescription('You can’t record Ahmed again until tomorrow.')
    expect(within(dialog).getByRole('button', { name: 'Keep writing' })).toHaveFocus()

    await user.click(within(dialog).getByRole('button', { name: 'Keep writing' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'What did they do?' })).toHaveValue('Draft')
    expect(cancel).toHaveFocus()

    await user.click(cancel)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(router.state.location.pathname).toBe(PATH)

    await user.click(cancel)
    await user.click(screen.getByRole('button', { name: 'Cancel report' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/report'))
  })

  it('keeps focus inside the dialog', async () => {
    setup()
    const user = userEvent.setup()
    renderApp(PATH)
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    await user.tab()
    expect(screen.getByRole('button', { name: 'Cancel report' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Keep writing' })).toHaveFocus()
  })
})

describe('the doer name', () => {
  it('uses the first name in short sentences', async () => {
    setup({ scan: makeScanDetail({ doer: { ...AHMED, display_name: 'Ahmed' } }) })
    renderApp(PATH)
    expect(
      await screen.findByText('It goes on Ahmed’s profile as soon as you send it.'),
    ).toBeVisible()
  })
})
