/**
 * QA11 (docs/M5-DESIGN.md §6.1, §6.3, D90): every M5 admin route is NotFound for members and
 * moderators without asking the admin API, and the case dialogs take the note minimum from
 * `/admin/fraud/options` instead of a number in the client.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, mockApi, renderApp, signedIn } from '../../test/utils'
import { ADMIN, makeCaseDetail, makeFraudOptions } from './fraudTestFixtures'

const M5_ROUTES = [
  '/admin/fraud',
  '/admin/fraud/case-1',
  '/admin/appeals',
  '/admin/appeals/appeal-1',
  '/admin/audit',
  '/admin/scoring/deeds/deed-1',
  '/admin/users/priya',
  '/admin/scoring/override-requests',
]

describe('M5 admin routes for people who are not admins', () => {
  type RouteCase = [role: 'member' | 'moderator', path: string]
  const cases: RouteCase[] = M5_ROUTES.flatMap((path): RouteCase[] => [
    ['member', path],
    ['moderator', path],
  ])
  it.each(cases)('a %s opening %s gets NotFound and no admin request', async (role, path) => {
    const api = mockApi({ ...signedIn(makeMe({ role, handle: 'someone' })) })
    renderApp(path)
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(api.requests.some((r) => r.path.startsWith('/admin/'))).toBe(false)
  })
})

describe('note limits come from the options', () => {
  function review(min: number) {
    return mockApi({
      ...signedIn(
        makeMe({ role: 'admin', handle: ADMIN.handle, display_name: ADMIN.display_name }),
      ),
      'GET /admin/fraud/options': { body: makeFraudOptions({ note_min_chars: min }) },
      'GET /admin/fraud/cases/case-1': { body: makeCaseDetail() },
      'POST /admin/fraud/cases/case-1/park': {
        body: makeCaseDetail({ case: { status: 'parked' } }),
      },
    })
  }

  it('refuses a note shorter than note_min_chars and sends one of exactly that length trimmed', async () => {
    const api = review(25)
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    await user.click(await screen.findByRole('button', { name: 'Park' }))
    const dialog = screen.getByRole('dialog', { name: 'Park this case?' })
    const box = within(dialog).getByRole('textbox')

    await user.type(box, 'x'.repeat(24))
    await user.click(within(dialog).getByRole('button', { name: 'Park case' }))
    expect(
      await within(dialog).findByText('Write a note of at least 25 characters.'),
    ).toBeInTheDocument()
    expect(api.calls('POST /admin/fraud/cases/case-1/park')).toHaveLength(0)

    await user.clear(box)
    await user.type(box, `   ${'y'.repeat(25)}   `)
    await user.click(within(dialog).getByRole('button', { name: 'Park case' }))
    await waitFor(() =>
      expect(api.calls('POST /admin/fraud/cases/case-1/park')[0]?.body).toEqual({
        note: 'y'.repeat(25),
      }),
    )
  })

  it('accepts a shorter note when the options allow it', async () => {
    const api = review(5)
    const user = userEvent.setup()
    renderApp('/admin/fraud/case-1')
    await user.click(await screen.findByRole('button', { name: 'Park' }))
    const dialog = screen.getByRole('dialog', { name: 'Park this case?' })
    await user.type(within(dialog).getByRole('textbox'), 'Short')
    await user.click(within(dialog).getByRole('button', { name: 'Park case' }))
    await waitFor(() =>
      expect(api.calls('POST /admin/fraud/cases/case-1/park')[0]?.body).toEqual({ note: 'Short' }),
    )
  })
})
