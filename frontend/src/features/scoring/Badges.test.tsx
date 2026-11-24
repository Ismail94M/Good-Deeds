/** Achievement medals (PO 2026-11-24): BadgeShelf, the ladder helpers and "Your badges". */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { BadgeShelf } from './BadgeShelf'
import { badgeInfo, ladderText, nextBadges, readSeenBadges, writeSeenBadges } from './badges'
import { makeMyScoring, makeOptions, PRIYA } from './testFixtures'

const OFFERED = [
  {
    slug: 'first_recognised',
    label: 'First recognition',
    description: 'Recognised for a verified deed',
  },
  { slug: 'kind_5', label: '5 deeds', description: 'Recognised for 5 verified deeds' },
  { slug: 'kind_25', label: '25 deeds', description: 'Recognised for 25 verified deeds' },
  { slug: 'kind_100', label: '100 deeds', description: 'Recognised for 100 verified deeds' },
  { slug: 'first_witness', label: 'First witness', description: 'Recognised a deed you witnessed' },
  {
    slug: 'witness_10',
    label: '10 deeds recognised',
    description: 'Recognised 10 deeds you witnessed',
  },
  {
    slug: 'witness_50',
    label: '50 deeds recognised',
    description: 'Recognised 50 deeds you witnessed',
  },
]
const badge = (slug: string) => {
  const { label } = OFFERED.find((b) => b.slug === slug)!
  return { slug, label }
}

describe('badge ladders', () => {
  it('gives each known badge its ladder, level and mark, and any other slug a plain medal', () => {
    expect(badgeInfo(badge('first_recognised'))).toMatchObject({
      family: 'recognised',
      level: 0,
      step: 1,
      steps: 4,
    })
    expect(badgeInfo(badge('kind_100'))).toMatchObject({
      family: 'recognised',
      level: 3,
      mark: '100',
    })
    expect(badgeInfo(badge('witness_50'))).toMatchObject({
      family: 'witness',
      level: 2,
      step: 3,
      steps: 3,
    })
    expect(ladderText(badgeInfo(badge('kind_25')))).toBe('Kindness · Silver · level 3 of 4')
    const other = badgeInfo({ slug: 'helper_of_the_year', label: 'Helper of the year' })
    expect(other).toMatchObject({ family: 'other', level: 0, description: 'Earned on Good Deeds' })
    // The server's explanation wins over the built-in wording.
    expect(badgeInfo(badge('kind_5'), 'From config').description).toBe('From config')
  })

  it('suggests the lowest badge not yet held on each ladder, only from what config offers', () => {
    const next = nextBadges(
      [badge('first_recognised'), badge('kind_5'), badge('first_witness')],
      OFFERED,
    )
    expect(next.map((b) => b.slug)).toEqual(['kind_25', 'witness_10'])
    expect(nextBadges([], OFFERED).map((b) => b.slug)).toEqual([
      'first_recognised',
      'first_witness',
    ])
    const all = OFFERED.map(({ slug, label }) => ({ slug, label }))
    expect(nextBadges(all, OFFERED)).toEqual([])
    // A badge removed from config is never suggested.
    expect(
      nextBadges(
        [badge('first_recognised')],
        OFFERED.filter((b) => b.slug !== 'kind_5'),
      )[0].slug,
    ).toBe('kind_25')
  })

  it('remembers celebrated badges per person on this device, surviving bad storage', () => {
    expect(readSeenBadges('priyas')).toBeNull()
    writeSeenBadges('priyas', ['kind_5', 'first_recognised', 'kind_5'])
    expect(readSeenBadges('priyas')).toEqual(['first_recognised', 'kind_5'])
    window.localStorage.setItem('gd.badges.seen.priyas', '{not json')
    expect(readSeenBadges('priyas')).toBeNull()
  })
})

describe('BadgeShelf', () => {
  it('shows each badge as a medal with its ladder colour and level, and the empty text', () => {
    const { rerender } = render(
      <BadgeShelf
        badges={[
          badge('first_recognised'),
          badge('kind_25'),
          badge('kind_100'),
          badge('witness_10'),
        ]}
      />,
    )
    const medals = screen.getAllByTestId('badge-medal')
    expect(medals.map((m) => [m.dataset.family, m.dataset.level])).toEqual([
      ['recognised', 'starter'],
      ['recognised', 'silver'],
      ['recognised', 'gold'],
      ['witness', 'bronze'],
    ])
    expect(medals[1]).toHaveTextContent('25 deeds')
    expect(medals[1]).toHaveAccessibleDescription(
      'Recognised for 25 verified deeds. Kindness · Silver · level 3 of 4.',
    )
    rerender(<BadgeShelf badges={[]} emptyText="Nothing here." />)
    expect(screen.getByText('Nothing here.')).toBeVisible()
  })

  it('opens the details on tap and closes on a second tap, Escape or a tap elsewhere', async () => {
    const user = userEvent.setup()
    render(
      <div>
        <p>Outside</p>
        <BadgeShelf badges={[badge('first_witness'), badge('kind_5')]} />
      </div>,
    )
    const [witness, five] = screen.getAllByTestId('badge-medal')
    await user.click(witness)
    expect(witness).toHaveAttribute('aria-expanded', 'true')
    const details = screen.getByTestId('badge-details')
    expect(details).toHaveTextContent('First witness')
    expect(details).toHaveTextContent('Recognised a deed you witnessed')
    expect(details).toHaveTextContent('Witness · Starter · level 1 of 3')
    await user.click(witness)
    expect(screen.queryByTestId('badge-details')).not.toBeInTheDocument()

    await user.click(five)
    expect(screen.getByTestId('badge-details')).toHaveTextContent('5 deeds')
    await user.keyboard('{Escape}')
    expect(screen.queryByTestId('badge-details')).not.toBeInTheDocument()

    await user.click(five)
    await user.click(screen.getByText('Outside'))
    expect(screen.queryByTestId('badge-details')).not.toBeInTheDocument()
  })

  it('opens the details on mouse hover and closes when the mouse leaves', async () => {
    const user = userEvent.setup()
    render(<BadgeShelf badges={[badge('kind_100')]} />)
    const medal = screen.getByTestId('badge-medal')
    await user.hover(medal)
    expect(screen.getByTestId('badge-details')).toHaveTextContent('Kindness · Gold · level 4 of 4')
    await user.unhover(medal)
    expect(screen.queryByTestId('badge-details')).not.toBeInTheDocument()
  })

  it('animates only fresh badges, and shows locked next badges greyed with how to earn them', () => {
    render(
      <BadgeShelf
        badges={[badge('first_recognised'), badge('kind_5')]}
        fresh={new Set(['kind_5'])}
        locked={nextBadges([badge('first_recognised'), badge('kind_5')], OFFERED)}
      />,
    )
    const medals = screen.getAllByTestId('badge-medal')
    expect(medals.map((m) => m.dataset.fresh ?? null)).toEqual([null, 'true', null, null])
    expect(medals[1]).toHaveTextContent('(new)')
    expect(medals[2]).toHaveAttribute('data-locked', 'true')
    expect(medals[2]).toHaveTextContent('Next25 deeds')
    expect(medals[2]).toHaveAccessibleDescription(
      'Locked. Next on your Kindness path: Recognised for 25 verified deeds.',
    )
  })
})

describe('Your badges on my profile', () => {
  const ME = makeMe({ handle: PRIYA.handle, display_name: PRIYA.display_name })
  const setup = () =>
    mockApi({
      ...signedIn(ME),
      'GET /scoring/me': {
        body: makeMyScoring({
          badges_total: 2,
          badges: [badge('first_recognised'), badge('first_witness')],
        }),
      },
      'GET /scoring/options': { body: makeOptions({ badges: OFFERED }) },
    })

  it('shows my medals and the next badge on each ladder, celebrating new ones only once', async () => {
    writeSeenBadges(PRIYA.handle, ['first_recognised'])
    setup()
    const { unmount } = renderApp('/profile')
    const section = await screen.findByRole('region', { name: /^Your badges/ })
    await within(section).findByText('10 deeds recognised')
    const medals = within(section).getAllByTestId('badge-medal')
    const names = ['First recognition', 'First witness', 'Next5 deeds', 'Next10 deeds recognised']
    names.forEach((name, n) => expect(medals[n]).toHaveTextContent(name))
    expect(medals).toHaveLength(4)
    expect(medals.map((m) => m.dataset.fresh ?? null)).toEqual([null, 'true', null, null])
    expect(readSeenBadges(PRIYA.handle)).toEqual(['first_recognised', 'first_witness'])
    unmount()

    renderApp('/profile')
    const again = await screen.findByRole('region', { name: /^Your badges/ })
    expect(
      within(again)
        .getAllByTestId('badge-medal')
        .some((m) => m.dataset.fresh),
    ).toBe(false)
  })
})
