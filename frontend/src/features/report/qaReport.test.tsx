/**
 * QA4: Record a deed (F3 canvas, M2-DESIGN §5.3, §5.4). Limit warnings and reset dates in IST,
 * the "When?" limit, error copy at scan and deed time (the doer is named only from `doer`),
 * the Cancel dialog, and what the form shows after a deed is sent.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getPosition, permissionState } from '../../lib/geolocation'
import { makeMe, mockApi, renderApp, signedIn, type Reply } from '../../test/utils'
import type { DeedOptions, ScanDetail } from '../deeds/deedsApi'
import { makeMediaOptions } from '../media/testFixtures'
import {
  AHMED,
  GOOD_FIX,
  makeDeed,
  makeOptions,
  makeScan,
  makeScanDetail,
  SCAN_ID,
} from './testFixtures'

vi.mock('../../lib/geolocation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/geolocation')>()
  return { ...actual, getPosition: vi.fn(), permissionState: vi.fn() }
})

beforeEach(() => {
  vi.mocked(getPosition).mockReset().mockResolvedValue(GOOD_FIX)
  vi.mocked(permissionState).mockReset().mockResolvedValue('granted')
})

const PATH = `/report/scan/${SCAN_ID}`
/** 00:00 IST on 1 January 2027, written in UTC (still 31 December in UTC). */
const JAN_1_IN_UTC = '2026-12-31T18:30:00Z'

function setupForm({
  scan = makeScanDetail(),
  scanReply,
  options = makeOptions(),
  deedReply = { status: 201, body: makeDeed() },
  extra = {},
}: {
  scan?: ScanDetail
  scanReply?: Reply | Reply[]
  options?: DeedOptions | Reply[]
  deedReply?: Reply | Reply[]
  extra?: Parameters<typeof mockApi>[0]
} = {}) {
  return mockApi({
    ...signedIn(makeMe({ display_name: 'Priya Sharma' })),
    'GET /deeds/options': Array.isArray(options) ? options : { body: options },
    [`GET /scans/${SCAN_ID}`]: scanReply ?? { body: scan },
    'POST /deeds': deedReply,
    ...extra,
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

describe('hub: total limit banner (D20)', () => {
  const hub = (options: Partial<DeedOptions>) =>
    mockApi({ ...signedIn(makeMe()), 'GET /deeds/options': { body: makeOptions(options) } })

  it('formats the reset date in IST even when the server writes it in UTC', async () => {
    hub({ reports_remaining_this_month: 4, month_resets_at: JAN_1_IN_UTC })
    renderApp('/report')
    const lead = await screen.findByText('4 deeds left this month.')
    expect(lead.closest('[role="status"]')).toHaveTextContent(
      '4 deeds left this month. Resets on 1 January.',
    )
  })

  it('uses the configured limit and IST date in the maximum banner', async () => {
    hub({
      reports_remaining_this_month: 0,
      reporter_monthly_limit: 200,
      month_resets_at: JAN_1_IN_UTC,
    })
    renderApp('/report')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "You've recorded 200 deeds this month, the maximum. You can record more from 1 January.",
    )
  })

  it('keeps disabled options reachable by keyboard but inert (aria-disabled, no navigation)', async () => {
    hub({ reports_remaining_this_month: 0 })
    const user = userEvent.setup()
    const { router } = renderApp('/report')
    const scan = await screen.findByRole('button', { name: /Scan their QR/ })
    expect(scan).toHaveAttribute('aria-disabled', 'true')
    expect(scan).toHaveClass('opacity-50')
    scan.focus()
    await user.keyboard('{Enter}')
    await user.keyboard(' ')
    expect(router.state.location.pathname).toBe('/report')
  })

  it('refreshes the remaining count after a deed is sent', async () => {
    setupForm({
      options: [
        { body: makeOptions({ reports_remaining_this_month: 11 }) },
        { body: makeOptions({ reports_remaining_this_month: 10 }) },
      ],
    })
    const user = await (async () => {
      renderApp(PATH)
      return fillValid()
    })()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    await user.click(await screen.findByRole('link', { name: 'Record another deed' }))
    expect(await screen.findByText('10 deeds left this month.')).toBeVisible()
  })
})

describe('deed form: pair warning (D20)', () => {
  it('says "2 more times" at 2 and uses the IST reset date from the scan', async () => {
    setupForm({
      scan: makeScanDetail({ pair_reports_remaining: 2, month_resets_at: JAN_1_IN_UTC }),
    })
    renderApp(PATH)
    await screen.findByText('Recognising')
    expect(
      screen.getByText('You can record Ahmed 2 more times this month.').closest('[role="status"]'),
    ).toHaveTextContent('You can record Ahmed 2 more times this month. Resets on 1 January.')
  })

  it('shows no pair warning at 0 (the server refuses that scan instead)', async () => {
    setupForm({ scan: makeScanDetail({ pair_reports_remaining: 0 }) })
    renderApp(PATH)
    await screen.findByText('Recognising')
    expect(screen.queryByText(/more times? this month/)).not.toBeInTheDocument()
  })
})

describe('deed form: When? (qr_occurred_max_age_minutes)', () => {
  it('offers no Earlier choice when the maximum is under 15 minutes', async () => {
    setupForm({ options: makeOptions({ qr_occurred_max_age_minutes: 10 }) })
    renderApp(PATH)
    await screen.findByText('Recognising')
    expect(screen.getByRole('button', { name: 'Just now' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: 'Earlier' })).not.toBeInTheDocument()
    expect(screen.getByText('Up to 10 minutes before you scanned.')).toBeVisible()
  })

  it('stops at 90 minutes, and the longest choice stays inside the window', async () => {
    const scan = makeScanDetail({ scanned_at: '2026-11-17T10:00:00.000Z' })
    const api = setupForm({ scan, options: makeOptions({ qr_occurred_max_age_minutes: 90 }) })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Earlier' }))
    const select = screen.getByRole('combobox', { name: 'How long before you scanned?' })
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['About 15 minutes', 'About 30 minutes', 'About 1 hour'])
    expect(screen.getByText('Up to 90 minutes before you scanned.')).toBeVisible()
    await user.selectOptions(select, 'About 1 hour')
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    await screen.findByRole('heading', { name: 'Deed verified' })
    expect(api.calls('POST /deeds')[0].body).toMatchObject({
      occurred_at: '2026-11-17T09:00:00.000Z',
    })
  })

  it('switching back to Just now omits occurred_at again', async () => {
    const api = setupForm()
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Earlier' }))
    await user.click(screen.getByRole('button', { name: 'Just now' }))
    expect(
      screen.queryByRole('combobox', { name: 'How long before you scanned?' }),
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    await screen.findByRole('heading', { name: 'Deed verified' })
    expect(api.calls('POST /deeds')[0].body).not.toHaveProperty('occurred_at')
  })
})

describe('deed form: notice, photos and labels', () => {
  it('names a one-word doer in the feed notice and has no photo section with photos off', async () => {
    setupForm({ scan: makeScanDetail({ doer: { handle: 'ravi', display_name: 'Ravi' } }) })
    renderApp(PATH)
    const form = await screen.findByRole('form', { name: 'Describe the deed' })
    expect(form).toHaveTextContent(
      'Verified deeds appear in the public feed. You or Ravi can hide it at any time. Only the area name is shown, never your exact location.',
    )
    expect(within(form).queryByText(/photo/i)).not.toBeInTheDocument()
    // The notice is the last thing before the Send block.
    const buttons = within(form).getAllByRole('button')
    expect(buttons.at(-1)).toHaveAccessibleName('Send report')
  })

  it('renders no photo controls when /media/options has photos switched off', async () => {
    setupForm({
      extra: { 'GET /media/options': { body: makeMediaOptions({ photos_enabled: false }) } },
    })
    renderApp(PATH)
    await screen.findByText('Recognising')
    expect(screen.queryByRole('button', { name: /Take photo|Upload/i })).not.toBeInTheDocument()
    expect(document.querySelector('input[type="file"]')).toBeNull()
  })

  it('makes every pill, stepper and Send a 44 px target', async () => {
    setupForm()
    renderApp(PATH)
    const form = await screen.findByRole('form', { name: 'Describe the deed' })
    for (const button of within(form).getAllByRole('button')) {
      expect(button.className).toMatch(/\b(min-h-1[1-3]|size-1[1-3])\b/)
    }
    expect(screen.getByRole('button', { name: 'Cancel' }).className).toMatch(/\bmin-h-11\b/)
  })
})

describe('deed form: more deed-time errors (§5.4)', () => {
  const error = (status: number, code: string, extra: Record<string, unknown> = {}) => ({
    status,
    body: { detail: `detail for ${code}`, code, ...extra },
  })

  it('shows Too many tries for a 429 rate_limited on Send', async () => {
    setupForm({ deedReply: error(429, 'rate_limited') })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('heading', { name: 'Too many tries' })).toBeVisible()
    expect(screen.getByText('Please wait a few minutes, then try again.')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to Home' })).toHaveAttribute('href', '/')
  })

  it('shows the report-link screen for scan_not_found on Send', async () => {
    setupForm({ deedReply: error(404, 'scan_not_found') })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(
      await screen.findByRole('heading', { name: "This report link isn't valid" }),
    ).toBeVisible()
    expect(
      within(screen.getByRole('main')).getByRole('link', { name: 'Record a deed' }),
    ).toHaveAttribute('href', '/report')
  })

  it.each(['invalid_category', 'invalid_description', 'invalid_beneficiaries'])(
    'shows %s inline with the API detail and keeps the form',
    async (code) => {
      setupForm({ deedReply: error(400, code) })
      renderApp(PATH)
      const user = await fillValid()
      await user.click(screen.getByRole('button', { name: 'Send report' }))
      expect(await screen.findByRole('alert')).toHaveTextContent(`detail for ${code}`)
      expect(screen.getByRole('textbox', { name: 'What did they do?' })).toHaveValue(
        'Helped an elderly man cross the road.',
      )
    },
  )

  it('uses the scan’s doer at deed time even if the body names someone else', async () => {
    setupForm({
      deedReply: error(409, 'already_recognized_today', {
        doer: { handle: 'x', display_name: 'Someone Else' },
      }),
    })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    expect(
      await screen.findByRole('heading', { name: "You've already recognised Ahmed today" }),
    ).toBeVisible()
    expect(screen.queryByText(/Someone/)).not.toBeInTheDocument()
  })
})

describe('Cancel dialog (Q19)', () => {
  it('cycles focus backwards too, and a click outside keeps writing', async () => {
    setupForm()
    const user = userEvent.setup()
    const { router } = renderApp(PATH)
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    const keep = screen.getByRole('button', { name: 'Keep writing' })
    expect(keep).toHaveFocus()
    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Cancel report' })).toHaveFocus()
    await user.tab({ shift: true })
    expect(keep).toHaveFocus()

    // The backdrop is the dialog's parent.
    await user.click(screen.getByRole('dialog').parentElement as HTMLElement)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(router.state.location.pathname).toBe(PATH)
  })

  it('uses the doer’s first name and lists Keep writing first', async () => {
    setupForm()
    const user = userEvent.setup()
    renderApp(PATH)
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    const dialog = screen.getByRole('dialog', { name: 'Cancel this report?' })
    expect(
      within(dialog)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Keep writing', 'Cancel report'])
    expect(dialog).toHaveTextContent('You can’t record Ahmed again until tomorrow.')
  })
})

describe('after sending', () => {
  it('opens I reported from See my reports', async () => {
    setupForm({
      extra: {
        'GET /me/deeds': { body: { items: [], next_cursor: null } },
      },
    })
    renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    await user.click(await screen.findByRole('link', { name: 'See my reports' }))
    expect(await screen.findByRole('tab', { name: 'I reported' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  // BUG (Low): after a deed is sent, the cached scan still says `consumed: false` (staleTime
  // 30 s), so going Back to `/report/scan/:id` within 30 s shows the deed form again instead of
  // the `scan_used` screen (§5.3 Deed form: "`consumed: true` → the scan_used screen"). Sending
  // it again only then fails with 409 scan_used.
  it('shows "This report was already sent" when going back to a sent scan', async () => {
    setupForm({
      scanReply: [{ body: makeScanDetail() }, { body: makeScanDetail({ consumed: true }) }],
      extra: { 'GET /deeds/options': { body: makeOptions() } },
    })
    const { router } = renderApp(PATH)
    const user = await fillValid()
    await user.click(screen.getByRole('button', { name: 'Send report' }))
    await user.click(await screen.findByRole('link', { name: 'Record another deed' }))
    await screen.findByRole('heading', { name: 'Record a deed' })
    await router.navigate(-1)
    expect(
      await screen.findByRole('heading', { name: 'This report was already sent' }),
    ).toBeVisible()
  })
})

describe('scan-time errors name the doer only from the body’s doer (D27)', () => {
  function scanApi(reply: Reply) {
    return mockApi({
      ...signedIn(makeMe()),
      'GET /deeds/options': { body: makeOptions({ month_resets_at: JAN_1_IN_UTC }) },
      'POST /scans': reply,
      [`GET /scans/${SCAN_ID}`]: { body: makeScanDetail() },
    })
  }
  async function submitCode() {
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Their ID'), 'GD-7K3M9Q0')
    await user.type(screen.getByLabelText('6-digit code'), '482913')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
  }

  it('already_recognized_today without doer: no name', async () => {
    scanApi({ status: 409, body: { detail: 'x', code: 'already_recognized_today' } })
    renderApp('/report/code')
    await submitCode()
    expect(
      await screen.findByRole('heading', { name: "You've already recognised this person today" }),
    ).toBeVisible()
    expect(
      screen.getByText(
        'You can recognise the same person once a day. You can recognise them again tomorrow, or record someone else now.',
      ),
    ).toBeVisible()
  })

  it('pair_monthly_limit without doer: no name, IST date from options', async () => {
    scanApi({ status: 429, body: { detail: 'x', code: 'pair_monthly_limit' } })
    renderApp('/report/code')
    await submitCode()
    expect(
      await screen.findByRole('heading', {
        name: "You've recorded this person 15 times this month",
      }),
    ).toBeVisible()
    expect(
      screen.getByText(
        "That's the most for one person. You can record them again from 1 January, and you can still record anyone else.",
      ),
    ).toBeVisible()
  })

  it('ignores a malformed doer in the body', async () => {
    scanApi({
      status: 409,
      body: { detail: 'x', code: 'already_recognized_today', doer: { handle: 'ahmedk' } },
    })
    renderApp('/report/code')
    await submitCode()
    expect(
      await screen.findByRole('heading', { name: "You've already recognised this person today" }),
    ).toBeVisible()
  })

  it.each([
    ['qr_invalid', 404, 'This code has expired'],
    ['already_scanned', 409, "You've already scanned this code"],
    ['cannot_verify_self', 409, "You can't record your own deed"],
  ])('%s never names anyone, even if a doer were sent', async (code, status, title) => {
    scanApi({ status, body: { detail: 'x', code, doer: AHMED } })
    renderApp('/report/code')
    await submitCode()
    expect(await screen.findByRole('heading', { name: title })).toBeVisible()
    expect(screen.getByRole('main')).not.toHaveTextContent(/Ahmed/)
  })

  it('the /scan/:token landing names the doer from the body too', async () => {
    scanApi({
      status: 429,
      body: { detail: 'x', code: 'pair_monthly_limit', doer: AHMED },
    })
    renderApp('/scan/7K3M9Q0.123.abc')
    expect(
      await screen.findByRole('heading', { name: "You've recorded Ahmed 15 times this month" }),
    ).toBeVisible()
  })

  it('a successful scan goes to the form with the scan’s doer and pair count', async () => {
    scanApi({ status: 201, body: makeScan({ pair_reports_remaining: 3 }) })
    renderApp('/report/code')
    await submitCode()
    expect(await screen.findByText('Recognising')).toBeVisible()
    expect(screen.getByText('You can record Ahmed 3 more times this month.')).toBeVisible()
    await waitFor(() => expect(screen.getByRole('timer')).toBeVisible())
  })
})
