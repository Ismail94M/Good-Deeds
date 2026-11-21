import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makeOptions } from './testFixtures'

function setup(remaining: number) {
  return mockApi({
    ...signedIn(makeMe()),
    'GET /deeds/options': { body: makeOptions({ reports_remaining_this_month: remaining }) },
  })
}

describe('ReportHubPage', () => {
  it('shows both options, the guidance line and how it works, with no invite option', async () => {
    setup(140)
    renderApp('/report')

    expect(await screen.findByRole('heading', { name: 'Record a deed' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Saw someone do something good? Recognise them while you’re both still there.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Scan their QR/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Enter ID and code/ })).toBeEnabled()
    expect(
      screen.getByText('Doer not on Good Deeds? Ask them to sign up, then show you their QR.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'How it works' })).toBeInTheDocument()
    expect(screen.getByText('Say what they did. It’s verified straight away.')).toBeInTheDocument()
    expect(screen.queryByText(/invite/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows no banner at 11 left', async () => {
    setup(11)
    renderApp('/report')
    await screen.findByRole('heading', { name: 'Record a deed' })
    expect(screen.queryByText(/left this month/)).not.toBeInTheDocument()
  })

  it('warns at 10 left with the reset date', async () => {
    setup(10)
    renderApp('/report')
    expect(
      (await screen.findByText('10 deeds left this month.')).closest('[role]'),
    ).toHaveAttribute('role', 'status')
    expect(screen.getByRole('status')).toHaveTextContent(
      '10 deeds left this month. Resets on 1 December.',
    )
  })

  it('takes the warning threshold from the deed options (config)', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /deeds/options': {
        body: makeOptions({ reports_remaining_this_month: 20, reporter_monthly_warn_at: 25 }),
      },
    })
    renderApp('/report')
    expect(await screen.findByText('20 deeds left this month.')).toBeInTheDocument()
  })

  it('says "1 deed left" in the singular', async () => {
    setup(1)
    renderApp('/report')
    expect(await screen.findByText('1 deed left this month.')).toBeInTheDocument()
  })

  it('disables both options at the monthly maximum', async () => {
    setup(0)
    const user = userEvent.setup()
    const { router } = renderApp('/report')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "You've recorded 150 deeds this month, the maximum. You can record more from 1 December.",
    )
    const scan = screen.getByRole('button', { name: /Scan their QR/ })
    const manual = screen.getByRole('button', { name: /Enter ID and code/ })
    expect(scan).toHaveAttribute('aria-disabled', 'true')
    expect(manual).toHaveAttribute('aria-disabled', 'true')
    await user.click(scan)
    await user.click(manual)
    expect(router.state.location.pathname).toBe('/report')
  })

  it('opens the scan and ID + code pages', async () => {
    setup(140)
    const user = userEvent.setup()
    const { router } = renderApp('/report')
    await user.click(await screen.findByRole('button', { name: /Enter ID and code/ }))
    expect(router.state.location.pathname).toBe('/report/code')

    await router.navigate('/report')
    await user.click(await screen.findByRole('button', { name: /Scan their QR/ }))
    expect(router.state.location.pathname).toBe('/report/scan')
  })

  it('shows a retryable error when the options can’t load', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      'GET /deeds/options': [{ status: 500, body: { detail: 'boom' } }, { body: makeOptions() }],
    })
    const user = userEvent.setup()
    renderApp('/report')

    expect(await screen.findByRole('heading', { name: 'Something went wrong' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: /Scan their QR/ })).toBeInTheDocument()
    await waitFor(() => expect(api.calls('GET /deeds/options')).toHaveLength(2))
  })

  it('sends a witness who isn’t signed in to log in first', async () => {
    mockApi()
    const { router } = renderApp('/report')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent('/report')}`)
  })
})
