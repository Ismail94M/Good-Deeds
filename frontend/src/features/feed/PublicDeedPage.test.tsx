import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { makePhoto, makePublic } from './testFixtures'

const NEXT = encodeURIComponent('/deeds/d1')

describe('PublicDeedPage', () => {
  it('shows the deed without action buttons, with the join section', async () => {
    mockApi({ 'GET /public/deeds/d1': { body: makePublic() } })
    renderApp('/d/d1')
    const card = await screen.findByRole('article', { name: 'Deed by Ahmed Khan' })
    expect(screen.getByText('Someone shared this good deed with you.')).toBeInTheDocument()
    expect(within(card).getByText('recognised by Priya Sharma')).toBeInTheDocument()
    expect(within(card).getByText('Helping an elderly person')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: '24 likes · 5 comments' })).toHaveAttribute(
      'href',
      `/signup?next=${NEXT}`,
    )
    expect(within(card).queryByRole('button', { name: /Like|Save|Share|Follow/ })).toBeNull()
    expect(screen.getByRole('heading', { name: 'See more good deeds like this' })).toBeVisible()
    expect(
      screen.getByText(
        'Join Good Deeds to like, comment, follow Ahmed and explore other people’s deeds.',
      ),
    ).toBeInTheDocument()
    const main = screen.getByRole('main')
    expect(within(main).getByRole('link', { name: 'Join Good Deeds' })).toHaveAttribute(
      'href',
      `/signup?next=${NEXT}`,
    )
    expect(within(main).getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      `/login?next=${NEXT}`,
    )
    expect(
      screen.getByText(/Exact locations and contact details are never shown\./),
    ).toBeInTheDocument()
  })

  it('offers Sign in and Join in the header, returning to the deed', async () => {
    mockApi({ 'GET /public/deeds/d1': { body: makePublic() } })
    renderApp('/d/d1')
    await screen.findByRole('article')
    const header = screen.getByRole('banner')
    expect(within(header).getByRole('link', { name: 'Join' })).toHaveAttribute(
      'href',
      `/signup?next=${NEXT}`,
    )
    expect(within(header).getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      `/login?next=${NEXT}`,
    )
  })

  it('shows only the remaining photos (Q33)', async () => {
    mockApi({
      'GET /public/deeds/d1': { body: makePublic({ photos: [makePhoto(1), makePhoto(3)] }) },
    })
    renderApp('/d/d1')
    const carousel = await screen.findByRole('region', { name: 'Photos' })
    expect(within(carousel).getByText('1 / 2')).toBeInTheDocument()
    expect(within(carousel).getByTestId('carousel-dots').children).toHaveLength(2)
  })

  it('shows the unavailable state for any 404', async () => {
    mockApi({
      'GET /public/deeds/d1': {
        status: 404,
        body: { detail: 'This deed isn’t available.', code: 'deed_unavailable' },
      },
    })
    renderApp('/d/d1')
    expect(await screen.findByRole('heading', { name: 'This deed isn’t available' })).toBeVisible()
    expect(
      screen.getByText('It may have been hidden or removed by the people involved.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Discover Good Deeds' })).toHaveAttribute('href', '/')
  })

  it('sends a signed-in visitor to /deeds/:id', async () => {
    mockApi({ ...signedIn(makeMe()), 'GET /feed/deeds/d1': { status: 404, body: {} } })
    const { router } = renderApp('/d/d1')
    await waitFor(() => expect(router.state.location.pathname).toBe('/deeds/d1'))
  })
})
