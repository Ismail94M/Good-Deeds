import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router'
import { ErrorAlert, FieldError, Loading } from '../../components/ui'
import { errorMessage } from '../../lib/api'
import { GeolocationError, getPosition, type Location } from '../../lib/geolocation'
import { useAuth } from '../auth/authContext'
import { CategoryPills } from '../deeds/CategoryPills'
import { useDeedOptions } from '../deeds/deedQueries'
import {
  deedsApi,
  deedsKeys,
  type DeedOptions,
  type SelfLogCreate,
  type SelfLogOut,
  type SelfLogUpdate,
} from '../deeds/deedsApi'
import { DescriptionField } from '../deeds/DescriptionField'
import { BackLink, F6Main, F6Title, focusRing, LockIcon, primaryButtonClass } from '../deeds/deedUi'
import {
  DEFAULT_DESCRIPTION_MIN,
  fromInputs,
  toDateInput,
  toTimeInput,
  validateSelfLog,
} from './selfLogValues'

const LOCATION_BLOCKED = 'Location is blocked in your browser.'
const LOCATION_FAILED = 'We couldn’t find your location. Try again in a moment.'

/**
 * `/me/self-logs/new` and `/me/self-logs/:id/edit` (F6 canvas): category, what you did, when
 * (any past date and time), and an optional current location. Private, no points, no verify
 * button and no photos in M2 (D3, D23).
 */
export function SelfLogFormPage() {
  const { id } = useParams()
  const options = useDeedOptions()
  const list = useInfiniteQuery({
    queryKey: deedsKeys.selfLogs,
    queryFn: ({ pageParam }) => deedsApi.selfLogs(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: id !== undefined,
  })
  const existing = id ? list.data?.pages.flatMap((p) => p.items).find((l) => l.id === id) : null

  // There is no single-log endpoint: page through the list until the log turns up.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = list
  useEffect(() => {
    if (id && list.data && !existing && hasNextPage && !isFetchingNextPage) {
      void fetchNextPage()
    }
  }, [id, list.data, existing, hasNextPage, isFetchingNextPage, fetchNextPage])

  if (options.isPending) return <Loading />
  if (options.isError) {
    return (
      <F6Main>
        <BackLink to="/me/self-logs" label="Cancel" />
        <ErrorAlert>{errorMessage(options.error)}</ErrorAlert>
      </F6Main>
    )
  }
  if (id) {
    if (list.isError) {
      return (
        <F6Main>
          <BackLink to="/me/self-logs" label="Cancel" />
          <ErrorAlert>{errorMessage(list.error)}</ErrorAlert>
        </F6Main>
      )
    }
    if (!existing) {
      if (list.isPending || hasNextPage) return <Loading />
      return (
        <F6Main>
          <BackLink to="/me/self-logs" label="Self-logs" />
          <ErrorAlert>This self-log isn’t available. It may have been deleted.</ErrorAlert>
        </F6Main>
      )
    }
  }
  return (
    <SelfLogForm key={existing?.id ?? 'new'} options={options.data} existing={existing ?? null} />
  )
}

function SelfLogForm({ options, existing }: { options: DeedOptions; existing: SelfLogOut | null }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const whenHelpId = useId()
  const [now] = useState(() => new Date())
  const start = existing ? new Date(existing.occurred_at) : now
  const [category, setCategory] = useState<string | null>(existing?.category ?? null)
  const [description, setDescription] = useState(existing?.description ?? '')
  const [date, setDate] = useState(toDateInput(start))
  const [time, setTime] = useState(toTimeInput(start))
  const [showErrors, setShowErrors] = useState(false)
  // Location: the stored one (edit), a new one, or removed.
  const [storedLocation, setStoredLocation] = useState(existing?.has_location ?? false)
  const [location, setLocation] = useState<Location | null>(null)
  const [locating, setLocating] = useState(false)
  const [locationError, setLocationError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: (body: { create: SelfLogCreate } | { update: SelfLogUpdate }) =>
      'create' in body
        ? deedsApi.createSelfLog(body.create)
        : deedsApi.updateSelfLog(existing?.id ?? '', body.update),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deedsKeys.selfLogs })
      if (user) void queryClient.invalidateQueries({ queryKey: deedsKeys.counts(user.handle) })
      navigate('/me/self-logs')
    },
  })

  const descriptionMin = options.description_min ?? DEFAULT_DESCRIPTION_MIN
  const validate = () =>
    validateSelfLog({ category, description, date, time }, undefined, descriptionMin)
  const errors = showErrors ? validate() : {}

  const toggleLocation = async (on: boolean) => {
    setLocationError(null)
    if (!on) {
      setLocation(null)
      return
    }
    setLocating(true)
    try {
      setLocation(await getPosition())
    } catch (error) {
      setLocation(null)
      setLocationError(
        error instanceof GeolocationError && error.kind === 'denied'
          ? LOCATION_BLOCKED
          : LOCATION_FAILED,
      )
    } finally {
      setLocating(false)
    }
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const found = validate()
    if (Object.keys(found).length > 0 || !category) {
      setShowErrors(true)
      return
    }
    const occurredAt = (fromInputs(date, time) as Date).toISOString()
    if (!existing) {
      const create: SelfLogCreate = {
        category,
        description: description.trim(),
        occurred_at: occurredAt,
      }
      if (location) create.location = location
      save.mutate({ create })
      return
    }
    const update: SelfLogUpdate = {}
    if (category !== existing.category) update.category = category
    if (description.trim() !== existing.description) update.description = description.trim()
    if (Date.parse(occurredAt) !== Date.parse(existing.occurred_at)) update.occurred_at = occurredAt
    if (location) update.location = location
    else if (existing.has_location && !storedLocation) update.location = null
    if (Object.keys(update).length === 0) {
      navigate('/me/self-logs')
      return
    }
    save.mutate({ update })
  }

  const inputClass = `min-h-12 w-full rounded-lg border bg-white px-2.5 text-base text-slate-900 ${focusRing} ${
    errors.when ? 'border-2 border-red-700' : 'border-slate-300'
  }`

  return (
    <F6Main>
      <BackLink to="/me/self-logs" label="Cancel" />
      <div className="flex flex-col gap-1.5">
        <F6Title>{existing ? 'Edit self-log' : 'Log a deed'}</F6Title>
        <p className="flex items-center gap-2 text-slate-600">
          <LockIcon className="size-4.5 shrink-0" />
          Private. Only you can see it, and it doesn’t earn points.
        </p>
      </div>
      <form noValidate onSubmit={onSubmit} className="flex flex-col gap-6">
        <CategoryPills
          categories={options.categories}
          value={category}
          onChange={setCategory}
          error={errors.category}
        />
        <DescriptionField
          label="What did you do?"
          value={description}
          onChange={setDescription}
          max={options.description_max}
          placeholder="For example: fed the stray dogs on my street"
          error={errors.description}
        />
        <fieldset className="min-w-0" aria-describedby={whenHelpId}>
          <legend className="mb-2.5 font-semibold text-slate-900">When did it happen?</legend>
          <div className="grid grid-cols-2 gap-2">
            <div className="flex flex-col gap-1">
              <label htmlFor="selflog-date" className="text-sm font-medium text-slate-700">
                Date
              </label>
              <input
                id="selflog-date"
                type="date"
                value={date}
                max={toDateInput(now)}
                onChange={(e) => setDate(e.target.value)}
                aria-invalid={errors.when ? true : undefined}
                className={inputClass}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="selflog-time" className="text-sm font-medium text-slate-700">
                Time
              </label>
              <input
                id="selflog-time"
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
                aria-invalid={errors.when ? true : undefined}
                className={inputClass}
              />
            </div>
          </div>
          <p id={whenHelpId} className="mt-2 text-sm text-[#586579]">
            Any date in the past.
          </p>
          {errors.when && (
            <div className="mt-2">
              <FieldError id={`${whenHelpId}-error`}>{errors.when}</FieldError>
            </div>
          )}
        </fieldset>

        {storedLocation && !location ? (
          <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-3">
            <span>
              <span className="block font-semibold">Location added</span>
              <span className="block text-sm text-[#586579]">Only you can see it.</span>
            </span>
            <button
              type="button"
              onClick={() => setStoredLocation(false)}
              className={`min-h-11 rounded-lg px-3 font-semibold text-red-700 underline underline-offset-2 hover:bg-red-50 ${focusRing}`}
            >
              Remove
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-3">
              <span>
                <span className="block font-semibold">Add my current location</span>
                <span className="block text-sm text-[#586579]">
                  {locating ? 'Finding your location…' : 'Optional. Only you can see it.'}
                </span>
              </span>
              <input
                type="checkbox"
                role="switch"
                checked={location !== null}
                disabled={locating}
                onChange={(e) => void toggleLocation(e.target.checked)}
                className={`size-6 shrink-0 accent-brand-700 ${focusRing}`}
              />
            </label>
            {locationError && (
              <p role="alert" className="text-sm font-medium text-red-700">
                {locationError}
              </p>
            )}
          </div>
        )}

        {save.error && <ErrorAlert>{errorMessage(save.error)}</ErrorAlert>}
        <div className="flex flex-col gap-2.5">
          <button
            type="submit"
            disabled={save.isPending || locating}
            className={`${primaryButtonClass} min-h-13 text-[17px]`}
          >
            {save.isPending ? 'Saving…' : 'Save self-log'}
          </button>
        </div>
      </form>
    </F6Main>
  )
}
