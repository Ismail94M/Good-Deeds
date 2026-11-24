import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, NOT_ONBOARDED, renderApp, signedIn } from '../test/utils'

/** The site header (a page's own <header> inside <main> is also a banner in jsdom). */
const siteHeader = () => screen.getAllByRole('banner')[0]

describe('Layout header (D25)', () => {
  it('shows Home · My QR · Profile and a Record a deed button when signed in', async () => {
    mockApi(signedIn(makeMe()))
    renderApp('/profile')
    const header = siteHeader()
    const record = await within(header).findByRole('link', { name: 'Record a deed' })
    expect(record).toHaveAttribute('href', '/report')

    const nav = within(header).getByRole('navigation', { name: 'Main' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual(['Home', 'My QR', 'Profile'])
    expect(within(nav).getByRole('link', { name: 'Profile' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(record).not.toHaveAttribute('aria-current')
    expect(within(header).queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()
  })

  it('shows the Record a deed button on every page, including public ones', async () => {
    mockApi(signedIn(makeMe()))
    renderApp('/terms')
    const header = siteHeader()
    expect(await within(header).findByRole('link', { name: 'Record a deed' })).toBeVisible()
  })

  it('shows the button before onboarding is finished too', async () => {
    mockApi(signedIn(makeMe({ phone_e164: null, onboarding: NOT_ONBOARDED })))
    renderApp('/profile')
    const header = siteHeader()
    expect(await within(header).findByRole('link', { name: 'Record a deed' })).toHaveAttribute(
      'href',
      '/report',
    )
  })

  it('marks the button as the current page inside Record a deed', async () => {
    mockApi(signedIn(makeMe()))
    renderApp('/report')
    const header = siteHeader()
    expect(await within(header).findByRole('link', { name: 'Record a deed' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('shows only the sign-up link when signed out', async () => {
    mockApi()
    renderApp('/')
    const header = siteHeader()
    expect(await within(header).findByRole('link', { name: /create an account/i })).toBeVisible()
    expect(within(header).queryByRole('link', { name: 'Record a deed' })).not.toBeInTheDocument()
    expect(within(header).queryByRole('link', { name: 'My QR' })).not.toBeInTheDocument()
    expect(within(header).queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()
  })
})
