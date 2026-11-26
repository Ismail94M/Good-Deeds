import { useId, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import {
  Button,
  ErrorAlert,
  FieldError,
  Loading,
  Page,
  TextField,
  TextLink,
} from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { useAuth } from '../auth/authContext'
import { identityApi, type Me } from '../auth/identityApi'
import { collectErrors, validateDisplayName, validateHandle } from '../auth/validation'
import { initials } from '../report/format'
import { useSocial } from '../feed/feedQueries'
import { useMyScoring } from '../scoring/scoringQueries'
import { OwnBadges } from '../scoring/OwnBadges'
import { TierChip } from '../scoring/TierChip'
import { RankingsButton } from '../scoring/Rankings'
import { useReviewsUnread } from '../reviews/reviewsApi'
import { DeedCounts } from './DeedCounts'

const BIO_MAX = 280
const AREA_MAX = 80

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700'

/**
 * `/profile` (F6 canvas, M2-DESIGN §5.3 F6c): avatar, name and "@handle" (Q22: no city), the
 * deed counts linking to self-logs and My deeds, Log a deed and My QR, "Your activity", and
 * the Account section with the M1 details, Edit profile and Log out (D25). M4 (§6.3, D69) adds
 * the tier and "Your points", visible only here, to their owner.
 */
export function ProfilePage() {
  const { user } = useAuth()
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)
  if (!user) return <Loading />

  if (editing) {
    return (
      <Page title="Edit your profile">
        <ProfileForm
          me={user}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            setSaved(true)
          }}
        />
      </Page>
    )
  }

  return (
    <main className="mx-auto flex w-full max-w-[720px] flex-col gap-5 px-4 pt-5 pb-10 sm:px-6 sm:pt-10 sm:pb-14">
      <section className="flex flex-col items-center gap-2 pt-2 text-center">
        <span
          aria-hidden
          className="flex size-22 items-center justify-center rounded-full bg-brand-100 text-[30px] font-bold text-brand-900"
        >
          {initials(user.display_name)}
        </span>
        <h1 className="mt-1 text-[26px] leading-tight font-bold tracking-tight break-words text-slate-900 sm:text-[32px]">
          {user.display_name}
        </h1>
        <p className="text-slate-600">@{user.handle}</p>
        <OwnTier />
      </section>
      {saved && (
        <p role="status" className="text-center text-brand-700">
          Your profile has been saved.
        </p>
      )}
      <div className="flex flex-col gap-2">
        <DeedCounts handle={user.handle} />
        <RankingsButton handle={user.handle} title="Your rankings" />
      </div>
      <ConnectionsLine handle={user.handle} />
      <OwnPoints />
      <OwnBadges handle={user.handle} />
      <div className="grid grid-cols-2 gap-2">
        <Link
          to="/me/self-logs/new"
          className={`inline-flex min-h-12 items-center justify-center gap-1.5 rounded-lg bg-brand-700 px-3 font-semibold text-white shadow-sm hover:bg-brand-900 ${focusRing}`}
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="size-4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
          Log a deed
        </Link>
        <Link
          to="/me/qr"
          className={`inline-flex min-h-12 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 font-semibold text-slate-800 hover:bg-slate-50 ${focusRing}`}
        >
          My QR code
        </Link>
      </div>
      <ActivitySection />
      <AccountSection
        me={user}
        onEdit={() => {
          setSaved(false)
          setEditing(true)
        }}
      />
    </main>
  )
}

/** My tier chip under my name; hidden while loading or on error. */
function OwnTier() {
  const scoring = useMyScoring()
  if (!scoring.data) return null
  return <TierChip tier={scoring.data.tier} />
}

/**
 * "Your points: 124 (12 still provisional)", badges and Hero titles, and a link to My points
 * (M4-DESIGN §6.3). Owner only: points never appear on a public screen (D69). Hidden while
 * loading or on error, so the rest of the profile never waits for scoring.
 */
function OwnPoints() {
  const scoring = useMyScoring()
  if (!scoring.data) return null
  // The server sums doer and reporter points (§3.4 item 5); the client never adds them up.
  const { total_points: total, provisional_points, badges_total, titles_total } = scoring.data
  return (
    <section
      aria-label="Your points"
      className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-2xl border border-slate-200 bg-white px-4 py-3.5"
    >
      <div>
        <p className="font-semibold text-slate-900">
          Your points: {total}
          {provisional_points > 0 && (
            <span className="font-normal text-slate-600">
              {' '}
              ({provisional_points} still provisional)
            </span>
          )}
        </p>
        <p className="text-sm text-slate-600">
          {badges_total} {badges_total === 1 ? 'badge' : 'badges'} · {titles_total} Hero{' '}
          {titles_total === 1 ? 'title' : 'titles'} · Only you can see your points.
        </p>
      </div>
      <Link
        to="/me/points"
        className={`inline-flex min-h-11 items-center rounded font-semibold text-brand-700 underline-offset-2 hover:text-brand-900 hover:underline ${focusRing}`}
      >
        My points
      </Link>
    </section>
  )
}

/** "{n} followers · {n} following · Saved" (M3-DESIGN §6.3, own profile only). */
function ConnectionsLine({ handle }: { handle: string }) {
  const social = useSocial(handle)
  const n = (value: number | undefined) => (social.isError ? '–' : (value ?? '…'))
  const link = `rounded font-semibold text-brand-700 hover:text-brand-900 underline-offset-2 hover:underline inline-flex min-h-11 items-center ${focusRing}`
  const followers = social.data?.followers
  const following = social.data?.following
  return (
    <p className="-mt-2 flex flex-wrap items-center justify-center gap-x-2 text-[15px] text-slate-600">
      <Link to="/me/connections?tab=followers" className={link}>
        {n(followers)} {followers === 1 ? 'follower' : 'followers'}
      </Link>
      <span aria-hidden>·</span>
      <Link to="/me/connections?tab=following" className={link}>
        {n(following)} following
      </Link>
      <span aria-hidden>·</span>
      <Link to="/me/saved" className={link}>
        Saved
      </Link>
    </p>
  )
}

function SectionHeading({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="text-[13px] font-semibold tracking-wider text-slate-600 uppercase">
      {children}
    </h2>
  )
}

function ActivitySection() {
  const headingId = useId()
  const items = [
    { to: '/me/deeds', title: 'My deeds', body: 'Reports about you and reports you made' },
    { to: '/me/self-logs', title: 'Self-logs', body: 'Your private diary of good deeds' },
    // M5 §6.2: explanation requests and decision notices about your deeds.
    { to: '/me/reviews', title: 'Reviews', body: 'Questions and decisions about your deeds' },
  ]
  const unread = useReviewsUnread()
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <SectionHeading id={headingId}>Your activity</SectionHeading>
      <ul className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        {items.map((item, index) => (
          <li key={item.to} className={index > 0 ? 'border-t border-slate-200' : undefined}>
            <Link
              to={item.to}
              className={`flex items-center gap-3 px-4 py-3.5 text-slate-900 hover:bg-slate-50 ${focusRing}`}
            >
              <span className="grow">
                <span className="flex items-center gap-2 font-semibold">
                  {item.title}
                  {item.to === '/me/reviews' && unread && (
                    <>
                      <span aria-hidden className="size-2 rounded-full bg-red-600" />
                      <span className="sr-only">(new update)</span>
                    </>
                  )}
                </span>{' '}
                <span className="block text-sm text-slate-600">{item.body}</span>
              </span>
              <svg
                aria-hidden
                viewBox="0 0 24 24"
                className="size-5 shrink-0"
                fill="none"
                stroke="#586579"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 6l6 6-6 6" />
              </svg>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The M1 account details, shown only to their owner. */
function AccountDetails({ me }: { me: Me }) {
  return (
    <dl className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 text-sm">
      <div>
        <dt className="text-slate-600">Name</dt>
        <dd className="text-base break-words">{me.display_name}</dd>
      </div>
      <div>
        <dt className="text-slate-600">Handle</dt>
        <dd className="text-base">@{me.handle}</dd>
      </div>
      {me.area && (
        <div>
          <dt className="text-slate-600">Area</dt>
          <dd className="text-base">{me.area}</dd>
        </div>
      )}
      {me.bio && (
        <div>
          <dt className="text-slate-600">Bio</dt>
          <dd className="text-base whitespace-pre-line">{me.bio}</dd>
        </div>
      )}
      {me.onboarding.complete && me.short_id && (
        <div>
          <dt className="text-slate-600">Your ID</dt>
          <dd className="font-mono text-base">{me.short_id}</dd>
        </div>
      )}
      {me.email && (
        <div>
          <dt className="text-slate-600">Email</dt>
          <dd className="text-base break-all">{me.email}</dd>
        </div>
      )}
      <div>
        <dt className="text-slate-600">Phone (only you can see this)</dt>
        <dd className="text-base">
          {me.phone_e164 ? (
            <>
              {me.phone_e164}
              <span className="block text-sm text-slate-600">
                To change your number, please contact support.
              </span>
            </>
          ) : (
            <>
              Not verified yet. <TextLink to="/onboarding">Verify now</TextLink>
            </>
          )}
        </dd>
      </div>
    </dl>
  )
}

/** "Account": Log out lives here, not in the header (M2-DESIGN D25). */
function AccountSection({ me, onEdit }: { me: Me; onEdit: () => void }) {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const headingId = useId()
  const [loggingOut, setLoggingOut] = useState(false)

  function handleLogout() {
    setLoggingOut(true)
    // Leave first: once the session is gone this page's guard would send us to /login instead.
    navigate('/', { replace: true })
    void logout()
  }

  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <h2
        id={headingId}
        className="text-[13px] font-semibold tracking-wider text-slate-600 uppercase"
      >
        Account
      </h2>
      <AccountDetails me={me} />
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={onEdit}>
          Edit profile
        </Button>
        <TextLink to={`/u/${me.handle}`}>See your public profile</TextLink>
      </div>
      <button
        type="button"
        onClick={handleLogout}
        disabled={loggingOut}
        className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-5 font-semibold text-red-700 transition-colors hover:bg-red-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
      >
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="size-4.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10" />
        </svg>
        {loggingOut ? 'Logging out…' : 'Log out'}
      </button>
    </section>
  )
}

type Values = { display_name: string; handle: string; area: string; bio: string }
type Field = keyof Values

function ProfileForm({
  me,
  onCancel,
  onSaved,
}: {
  me: Me
  onCancel: () => void
  onSaved: () => void
}) {
  const { setMe } = useAuth()
  const bioId = useId()
  // A draft of the form; the saved profile itself lives in the TanStack Query cache.
  const [values, setValues] = useState<Values>({
    display_name: me.display_name,
    handle: me.handle,
    area: me.area ?? '',
    bio: me.bio ?? '',
  })
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function update(patch: Partial<Values>) {
    setValues((v) => ({ ...v, ...patch }))
    setErrors({})
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const found = collectErrors({
      display_name: validateDisplayName(values.display_name),
      handle: validateHandle(values.handle),
      area: values.area.trim().length > AREA_MAX ? 'Please keep your area short.' : null,
      bio: values.bio.length > BIO_MAX ? `Please keep your bio to ${BIO_MAX} characters.` : null,
    })
    setErrors(found)
    if (Object.keys(found).length) return
    setError(null)
    setSaving(true)
    try {
      setMe(
        await identityApi.updateMe({
          display_name: values.display_name.trim(),
          handle: values.handle.trim(),
          area: values.area.trim(),
          bio: values.bio.trim(),
        }),
      )
      onSaved()
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === 'handle_taken') setErrors({ handle: err.detail })
        else setErrors(err.fieldErrors)
      }
      setError(errorMessage(err))
      setSaving(false)
    }
  }

  const bioLeft = BIO_MAX - values.bio.length

  return (
    <form noValidate onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
      {error && <ErrorAlert>{error}</ErrorAlert>}
      <TextField
        label="Your name"
        name="display_name"
        autoComplete="name"
        value={values.display_name}
        onChange={(e) => update({ display_name: e.target.value })}
        error={errors.display_name}
      />
      <TextField
        label="Handle"
        name="handle"
        autoCapitalize="none"
        spellCheck={false}
        value={values.handle}
        onChange={(e) => update({ handle: e.target.value })}
        hint="3–30 letters, numbers, _ or ."
        error={errors.handle}
      />
      <TextField
        label="Area"
        name="area"
        placeholder="e.g. Koramangala"
        maxLength={AREA_MAX}
        value={values.area}
        onChange={(e) => update({ area: e.target.value })}
        hint={`City: ${me.city}`}
        error={errors.area}
      />
      <div className="space-y-1">
        <label htmlFor={bioId} className="block font-medium text-slate-800">
          Bio
        </label>
        <textarea
          id={bioId}
          name="bio"
          rows={4}
          maxLength={BIO_MAX}
          value={values.bio}
          onChange={(e) => update({ bio: e.target.value })}
          aria-describedby={`${bioId}-count`}
          className="block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-base focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700"
        />
        <p
          id={`${bioId}-count`}
          className={`text-right text-sm ${bioLeft < 20 ? 'text-amber-700' : 'text-slate-500'}`}
        >
          {values.bio.length}/{BIO_MAX}
        </p>
        {errors.bio && <FieldError id={`${bioId}-error`}>{errors.bio}</FieldError>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
