import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../../test/utils'

/** The site header (a page's own <header> inside <main> is also a banner in jsdom). */
const siteHeader = () => screen.getAllByRole('banner')[0]

// M4 D69: deed-counts is public and has `verified` only; my self-log count is `total` on my
// own `GET /me/self-logs`.
const COUNTS = {
  'GET /users/asha/deed-counts': { body: { verified: 12 } },
  'GET /me/self-logs': { body: { items: [], next_cursor: null, total: 25 } },
}

const MY_SCORING = {
  tier: { slug: 'sprout', label: 'Sprout' },
  badges_total: 2,
  titles_total: 1,
  badges: [
    { slug: 'first_recognised', label: 'First recognition' },
    { slug: 'kind_5', label: '5 deeds' },
  ],
  titles: [
    {
      label: 'Kindness Hero · Koramangala · November 2026',
      period_month: '2026-11',
      region_name: 'Koramangala',
      place: 1,
      level: 'ward',
    },
  ],
  total_points: 124,
  // Deliberately not adding up to total_points: the page shows the server's total (§3.4 item 5).
  doer_points: 100,
  reporter_points: 14,
  provisional_points: 12,
  boards: [],
  reporter_today: { used: 1, cap: 10 },
}

describe('ProfilePage', () => {
  it('shows the name, "@handle" without a city, and the linked counts', async () => {
    mockApi({
      ...signedIn(makeMe({ display_name: 'Asha Rao', area: 'Koramangala' })),
      ...COUNTS,
    })
    renderApp('/profile')

    expect(await screen.findByRole('heading', { level: 1, name: 'Asha Rao' })).toBeVisible()
    const main = screen.getByRole('main')
    // M6: the initials avatar in the profile header (the nav shows a small one too).
    expect(within(main).getByText('AR')).toBeInTheDocument()
    expect(within(main).getAllByText('@asha')[0]).toBeVisible()
    expect(screen.queryByText(/Bangalore/)).not.toBeInTheDocument()

    const counts = screen.getByRole('group', { name: 'Deed counts' })
    const selfLogs = await within(counts).findByRole('link', { name: '25 Self-log Deeds' })
    expect(selfLogs).toHaveAttribute('href', '/me/self-logs')
    expect(within(counts).getByRole('link', { name: '12 Verified Deeds' })).toHaveAttribute(
      'href',
      '/me/deeds',
    )
  })

  it('has Log a deed, My QR and the activity links', async () => {
    mockApi({ ...signedIn(makeMe()), ...COUNTS })
    renderApp('/profile')
    expect(await screen.findByRole('link', { name: 'Log a deed' })).toHaveAttribute(
      'href',
      '/me/self-logs/new',
    )
    expect(screen.getByRole('link', { name: 'My QR code' })).toHaveAttribute('href', '/me/qr')
    const activity = screen.getByRole('region', { name: 'Your activity' })
    expect(
      within(activity).getByRole('link', {
        name: /My deeds Reports about you and reports you made/,
      }),
    ).toHaveAttribute('href', '/me/deeds')
    expect(
      within(activity).getByRole('link', { name: /Self-logs Your private diary of good deeds/ }),
    ).toHaveAttribute('href', '/me/self-logs')
    // M3 adds the "{n} followers · {n} following · Saved" line (tested in feed/Connections.test).
    expect(within(activity).queryByRole('link', { name: /^Admin/ })).not.toBeInTheDocument()
    expect(within(activity).queryByRole('link', { name: /^Moderation/ })).not.toBeInTheDocument()
  })

  it('shows the way into the admin area only to admins and moderators', async () => {
    mockApi({ ...signedIn(makeMe({ role: 'admin' })), ...COUNTS })
    const { unmount } = renderApp('/profile')
    const activity = await screen.findByRole('region', { name: 'Your activity' })
    expect(within(activity).getByRole('link', { name: /^Admin/ })).toHaveAttribute(
      'href',
      '/admin/fraud',
    )
    unmount()

    mockApi({ ...signedIn(makeMe({ role: 'moderator' })), ...COUNTS })
    renderApp('/profile')
    const modActivity = await screen.findByRole('region', { name: 'Your activity' })
    expect(within(modActivity).getByRole('link', { name: /^Moderation/ })).toHaveAttribute(
      'href',
      '/admin/photo-reports',
    )
    expect(within(modActivity).queryByRole('link', { name: /^Admin/ })).not.toBeInTheDocument()
  })

  it('shows a dash when the counts can’t load', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /users/asha/deed-counts': { status: 500, body: { detail: 'boom' } },
      'GET /me/self-logs': { status: 500, body: { detail: 'boom' } },
    })
    renderApp('/profile')
    const counts = await screen.findByRole('group', { name: 'Deed counts' })
    expect(await within(counts).findByRole('link', { name: '– Self-log Deeds' })).toBeVisible()
    expect(await within(counts).findByRole('link', { name: '– Verified Deeds' })).toBeVisible()
  })

  it('hides the self-log number when the API sends no total (M4 D69)', async () => {
    mockApi({
      ...signedIn(makeMe()),
      'GET /users/asha/deed-counts': { body: { verified: 12 } },
      'GET /me/self-logs': { body: { items: [], next_cursor: null } },
    })
    renderApp('/profile')
    const counts = await screen.findByRole('group', { name: 'Deed counts' })
    expect(await within(counts).findByRole('link', { name: 'Self-logs' })).toHaveAttribute(
      'href',
      '/me/self-logs',
    )
    expect(within(counts).getByRole('link', { name: '12 Verified Deeds' })).toBeVisible()
  })

  it('shows my tier, points, badges and titles, only to me, with a link to My points', async () => {
    mockApi({ ...signedIn(makeMe()), ...COUNTS, 'GET /scoring/me': { body: MY_SCORING } })
    renderApp('/profile')
    const points = await screen.findByRole('region', { name: 'Your points' })
    expect(points).toHaveTextContent('Your points: 124 (12 still provisional)')
    expect(points).toHaveTextContent('2 badges · 1 Hero title · Only you can see your points.')
    expect(within(points).getByRole('link', { name: 'My points' })).toHaveAttribute(
      'href',
      '/me/points',
    )
    expect(screen.getByText('Sprout')).toHaveTextContent('Tier: Sprout')
  })

  it('leaves out the points line when scoring can’t load', async () => {
    mockApi({
      ...signedIn(makeMe()),
      ...COUNTS,
      'GET /scoring/me': { status: 500, body: { detail: 'boom' } },
    })
    renderApp('/profile')
    const counts = await screen.findByRole('group', { name: 'Deed counts' })
    await within(counts).findByRole('link', { name: '25 Self-log Deeds' })
    expect(screen.queryByRole('region', { name: 'Your points' })).not.toBeInTheDocument()
  })

  it('shows the account details with the phone number to its owner', async () => {
    mockApi({
      ...signedIn(makeMe({ area: 'Koramangala', bio: 'I like helping.' })),
      ...COUNTS,
    })
    renderApp('/profile')
    const account = await screen.findByRole('region', { name: 'Account' })
    expect(within(account).getByText('Koramangala')).toBeInTheDocument()
    expect(within(account).getByText('I like helping.')).toBeInTheDocument()
    expect(within(account).getByText('+919876543210')).toBeInTheDocument()
    expect(within(account).getByText('GD-7K3M9Q0')).toBeInTheDocument()
    expect(
      within(account).getByText(/to change your number, please contact support/i),
    ).toBeInTheDocument()
    expect(within(account).getByRole('link', { name: 'See your public profile' })).toHaveAttribute(
      'href',
      '/u/asha',
    )
  })

  it('does not show an ID before onboarding is complete', async () => {
    mockApi({ ...signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })), ...COUNTS })
    renderApp('/profile')
    const account = await screen.findByRole('region', { name: 'Account' })
    expect(within(account).queryByText('Your ID')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Verify now' })).toHaveAttribute('href', '/onboarding')
  })

  it('edits the profile and counts bio characters', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      ...COUNTS,
      'PATCH /users/me': { body: makeMe({ area: 'Indiranagar', bio: 'Hello' }) },
    })
    const user = userEvent.setup()
    renderApp('/profile')
    await user.click(await screen.findByRole('button', { name: 'Edit profile' }))

    expect(screen.getByRole('heading', { name: 'Edit your profile' })).toBeVisible()
    expect(screen.getByLabelText('Area')).toHaveAttribute('placeholder', 'e.g. Koramangala')
    await user.type(screen.getByLabelText('Area'), 'Indiranagar')
    await user.type(screen.getByLabelText('Bio'), 'Hello')
    expect(screen.getByText('5/280')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Your profile has been saved.')
    const account = screen.getByRole('region', { name: 'Account' })
    expect(within(account).getByText('Indiranagar')).toBeInTheDocument()
    expect(api.calls('PATCH /users/me')[0].body).toEqual({
      display_name: 'Asha',
      handle: 'asha',
      area: 'Indiranagar',
      bio: 'Hello',
    })
  })

  it('shows a taken handle next to the field', async () => {
    mockApi({
      ...signedIn(makeMe()),
      ...COUNTS,
      'PATCH /users/me': {
        status: 409,
        body: { detail: 'That handle is taken. Please choose another.', code: 'handle_taken' },
      },
    })
    const user = userEvent.setup()
    renderApp('/profile')
    await user.click(await screen.findByRole('button', { name: 'Edit profile' }))
    const handle = screen.getByLabelText('Handle')
    await user.clear(handle)
    await user.type(handle, 'ravi')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/that handle is taken/i)).toBeInTheDocument()
    expect(handle).toHaveAttribute('aria-invalid', 'true')
  })

  it('logs out from the Account section and leaves the header without Log out', async () => {
    const api = mockApi({
      ...signedIn(makeMe()),
      ...COUNTS,
      'POST /auth/logout': { status: 204 },
    })
    const user = userEvent.setup()
    const { router } = renderApp('/profile')

    const account = await screen.findByRole('region', { name: 'Account' })
    const header = siteHeader()
    expect(within(header).queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()

    await user.click(within(account).getByRole('button', { name: 'Log out' }))
    expect(
      await within(header).findByRole('link', { name: 'Create an account' }, { timeout: 2000 }),
    ).toBeVisible()
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(api.calls('POST /auth/logout')).toHaveLength(1)
  })
})
