import { useState } from 'react'
import type { AvatarUrls } from './avatarApi'
import { DefaultAvatarContent, SILHOUETTE_TONE, toneFor } from './DefaultAvatar'
import { useAvatarUrl } from './useAvatarUrls'

export const AVATAR_SIZES = {
  nav: 'size-6 text-[10px] sm:size-7 sm:text-[11px]',
  xs: 'size-7 text-[11px]',
  sm: 'size-9 text-[13px]',
  md: 'size-10 text-sm',
  lg: 'size-11 text-[15px]',
  row: 'size-12 text-base',
  card: 'size-13 text-lg',
  xl: 'size-22 text-[30px]',
  story: 'size-15 text-lg',
}

export type AvatarSize = keyof typeof AVATAR_SIZES

/** The variant per size (§6.2): small circles use `avatar_sm`, big ones `avatar_lg`. */
function urlFor(urls: AvatarUrls, size: AvatarSize): string {
  if (size === 'xl' || size === 'story') return urls.lg_url
  if (size === 'lg' || size === 'md' || size === 'card' || size === 'row') return urls.md_url
  return urls.sm_url
}

/** Any `UserRef`-like value: deeds, feed, scoring, fraud, reviews and admin refs all fit. */
export type AvatarPerson = { handle: string | null; display_name: string; avatar?: string | null }

const UNAVAILABLE_NAME = 'Unavailable account'

/** True for "Unavailable account" rows (SEC3 L6, M5 Q72), which never show initials (D120). */
export function isUnavailable(person: { handle: string | null; display_name: string }): boolean {
  return person.handle === null || person.display_name === UNAVAILABLE_NAME
}

/**
 * The one avatar used everywhere a person is named (D124). It always renders the default
 * (initials on the person's colour, or a silhouette for an unavailable account) and lays the
 * picture over it once its signed URL arrives, so loading and failures fall back to the default.
 * On an image error it asks for fresh URLs once; if that fails too, the default stays.
 * Decorative: the name is always shown next to it.
 */
export function Avatar({
  user,
  name,
  handle,
  avatar,
  unavailable,
  size = 'lg',
  className = '',
}: {
  user?: AvatarPerson
  name?: string
  handle?: string | null
  avatar?: string | null
  unavailable?: boolean
  size?: AvatarSize
  className?: string
}) {
  const displayName = name ?? user?.display_name ?? ''
  const personHandle = handle !== undefined ? handle : (user?.handle ?? null)
  const gone =
    unavailable ?? isUnavailable({ handle: personHandle ?? null, display_name: displayName })
  const ref = gone ? null : avatar !== undefined ? avatar : (user?.avatar ?? null)
  return (
    <span
      aria-hidden
      data-avatar={ref ? 'ref' : gone ? 'silhouette' : 'initials'}
      className={`relative flex shrink-0 items-center justify-center overflow-hidden rounded-full font-bold ${AVATAR_SIZES[size]} ${
        gone ? SILHOUETTE_TONE : toneFor(personHandle ?? displayName)
      } ${className}`}
    >
      <DefaultAvatarContent name={displayName} unavailable={gone} />
      {ref && <AvatarPicture avatarRef={ref} size={size} />}
    </span>
  )
}

function AvatarPicture({ avatarRef, size }: { avatarRef: string; size: AvatarSize }) {
  const { urls, refetch } = useAvatarUrl(avatarRef)
  const [broken, setBroken] = useState<string | null>(null)
  const [refreshed, setRefreshed] = useState(false)
  if (!urls) return null
  const src = urlFor(urls, size)
  if (src === broken) return null
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      draggable={false}
      className="absolute inset-0 size-full object-cover"
      onError={() => {
        setBroken(src)
        if (!refreshed) {
          setRefreshed(true)
          void refetch()
        }
      }}
    />
  )
}
