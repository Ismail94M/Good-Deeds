import { useId, useState, type FormEvent } from 'react'
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

const BIO_MAX = 280
const AREA_MAX = 80

export function ProfilePage() {
  const { user } = useAuth()
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)
  if (!user) return <Loading />

  return (
    <Page title="Your profile">
      {saved && !editing && (
        <p role="status" className="text-brand-700">
          Your profile has been saved.
        </p>
      )}
      {editing ? (
        <ProfileForm
          me={user}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            setSaved(true)
          }}
        />
      ) : (
        <ProfileView
          me={user}
          onEdit={() => {
            setSaved(false)
            setEditing(true)
          }}
        />
      )}
    </Page>
  )
}

function ProfileView({ me, onEdit }: { me: Me; onEdit: () => void }) {
  return (
    <div className="space-y-5">
      <section className="space-y-1 rounded-2xl border border-slate-200 bg-white p-4">
        <p className="text-xl font-semibold">{me.display_name}</p>
        <p className="text-slate-600">@{me.handle}</p>
        <p className="text-slate-600">
          {me.area ? `${me.area}, ` : ''}
          {me.city}
        </p>
        {me.bio ? (
          <p className="pt-2 whitespace-pre-line">{me.bio}</p>
        ) : (
          <p className="pt-2 text-slate-500">No bio yet. Tell people a little about yourself.</p>
        )}
      </section>
      <dl className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 text-sm">
        {me.onboarding.complete && me.short_id && (
          <div>
            <dt className="text-slate-500">Your ID</dt>
            <dd className="font-mono text-base">{me.short_id}</dd>
          </div>
        )}
        {me.email && (
          <div>
            <dt className="text-slate-500">Email</dt>
            <dd className="text-base break-all">{me.email}</dd>
          </div>
        )}
        <div>
          <dt className="text-slate-500">Phone (only you can see this)</dt>
          <dd className="text-base">
            {me.phone_e164 ? (
              <>
                {me.phone_e164}
                <span className="block text-sm text-slate-500">
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
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={onEdit}>Edit profile</Button>
        <TextLink to={`/u/${me.handle}`}>See your public profile</TextLink>
      </div>
    </div>
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
