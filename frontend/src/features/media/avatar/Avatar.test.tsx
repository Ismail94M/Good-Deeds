/**
 * M6 F26 (docs/M6-DESIGN.md §6.2, D117–D120): the shared avatar shows the default first (initials
 * on the person's colour, a silhouette for an unavailable account), swaps in the picture once one
 * batched `POST /media/avatars/urls` answers, refreshes once on an image error and then keeps the
 * default, and never asks for pictures when signed out.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { makeMe, mockApi } from '../../../test/utils'
import { AuthContext, type AuthContextValue } from '../../auth/authContext'
import { Avatar } from './Avatar'
import { toneFor } from './DefaultAvatar'
import { resetAvatarBatchForTests } from './useAvatarUrls'

afterEach(() => resetAvatarBatchForTests())

function urls(ref: string, expiresInMs = 24 * 60 * 60_000) {
  return {
    sm_url: `https://storage.test/${ref}/sm.webp`,
    md_url: `https://storage.test/${ref}/md.webp`,
    lg_url: `https://storage.test/${ref}/lg.webp`,
    expires_at: new Date(Date.now() + expiresInMs).toISOString(),
  }
}

function wrap(children: ReactNode, signedIn = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const auth = { user: signedIn ? makeMe() : null } as AuthContextValue
  return render(
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
    </QueryClientProvider>,
  )
}

const imgs = (container: HTMLElement) => container.querySelectorAll('img')

describe('Avatar', () => {
  it('shows initials on a stable colour when there is no picture', () => {
    const api = mockApi()
    const { container } = wrap(
      <>
        <Avatar user={{ handle: 'ahmedk', display_name: 'Ahmed Khan', avatar: null }} />
        <Avatar name="Ahmed Khan" handle="ahmedk" size="sm" />
      </>,
    )
    const [first, second] = container.querySelectorAll('[data-avatar]')
    expect(first).toHaveTextContent('AK')
    expect(first.className).toContain(toneFor('ahmedk'))
    // The same person gets the same colour at every size.
    expect(second.className).toContain(toneFor('ahmedk'))
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(0)
  })

  it('shows a neutral silhouette, never initials, for an unavailable account', () => {
    mockApi()
    const { container } = wrap(
      <Avatar user={{ handle: null, display_name: 'Unavailable account', avatar: 'ref-x' }} />,
    )
    expect(screen.getByTestId('avatar-silhouette')).toBeInTheDocument()
    expect(container).not.toHaveTextContent('UA')
    expect(imgs(container)).toHaveLength(0)
  })

  it('batches every ref on the screen into one call, then shows the pictures', async () => {
    const api = mockApi({
      'POST /media/avatars/urls': (req) => {
        const refs = (req.body as { refs: string[] }).refs
        return {
          body: {
            items: Object.fromEntries(refs.map((r) => [r, r === 'gone' ? null : urls(r)])),
          },
        }
      },
    })
    const { container } = wrap(
      <>
        <Avatar user={{ handle: 'priya', display_name: 'Priya', avatar: 'p1' }} size="sm" />
        <Avatar user={{ handle: 'ravi', display_name: 'Ravi', avatar: 'r1' }} size="xl" />
        <Avatar user={{ handle: 'priya', display_name: 'Priya', avatar: 'p1' }} />
        <Avatar user={{ handle: 'sam', display_name: 'Sam', avatar: 'gone' }} />
      </>,
    )
    // Initials while loading.
    expect(container).toHaveTextContent('P')
    await waitFor(() => expect(imgs(container)).toHaveLength(3))
    const calls = api.calls('POST /media/avatars/urls')
    expect(calls).toHaveLength(1)
    expect((calls[0].body as { refs: string[] }).refs.sort()).toEqual(['gone', 'p1', 'r1'])
    const sources = [...imgs(container)].map((img) => img.getAttribute('src'))
    // sm → avatar_sm, xl → avatar_lg, lg → avatar_md.
    expect(sources).toEqual([
      'https://storage.test/p1/sm.webp',
      'https://storage.test/r1/lg.webp',
      'https://storage.test/p1/md.webp',
    ])
    for (const img of imgs(container)) {
      expect(img).toHaveAttribute('alt', '')
      expect(img).toHaveAttribute('loading', 'lazy')
    }
  })

  it('refreshes once when the image fails, then keeps the default', async () => {
    const api = mockApi({
      'POST /media/avatars/urls': [
        { body: { items: { a1: urls('a1') } } },
        { body: { items: { a1: { ...urls('a1'), sm_url: 'https://storage.test/a1/sm-2.webp' } } } },
      ],
    })
    const { container } = wrap(
      <Avatar user={{ handle: 'asha', display_name: 'Asha', avatar: 'a1' }} size="sm" />,
    )
    await waitFor(() => expect(imgs(container)).toHaveLength(1))
    fireEvent.error(imgs(container)[0])
    await waitFor(() =>
      expect(imgs(container)[0]).toHaveAttribute('src', 'https://storage.test/a1/sm-2.webp'),
    )
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(2)
    fireEvent.error(imgs(container)[0])
    expect(imgs(container)).toHaveLength(0)
    expect(container).toHaveTextContent('A')
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(2)
  })

  it('never asks for pictures when signed out (D119)', async () => {
    const api = mockApi()
    const { container } = wrap(
      <Avatar user={{ handle: 'asha', display_name: 'Asha', avatar: 'a1' }} />,
      false,
    )
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(api.calls('POST /media/avatars/urls')).toHaveLength(0)
    expect(imgs(container)).toHaveLength(0)
    expect(container).toHaveTextContent('A')
  })

  it('keeps the default when the URL call fails', async () => {
    const api = mockApi({
      'POST /media/avatars/urls': { status: 429, body: { detail: 'Slow down' } },
    })
    const { container } = wrap(
      <Avatar user={{ handle: 'asha', display_name: 'Asha', avatar: 'a1' }} />,
    )
    await waitFor(() => expect(api.calls('POST /media/avatars/urls')).toHaveLength(1))
    expect(imgs(container)).toHaveLength(0)
    expect(container).toHaveTextContent('A')
  })
})
