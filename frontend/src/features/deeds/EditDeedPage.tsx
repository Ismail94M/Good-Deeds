import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ErrorAlert, Loading } from '../../components/ui'
import { ApiError, errorMessage } from '../../lib/api'
import { formatOccurredAt } from '../report/format'
import { CategoryPills } from './CategoryPills'
import type { DeedDetailState } from './DeedDetailPage'
import { useDeedOptions, useInvalidateDeeds } from './deedQueries'
import { deedsApi, deedsKeys, type DeedOut, type DeedUpdate } from './deedsApi'
import { DescriptionField } from './DescriptionField'
import {
  BackLink,
  F6Main,
  F6Title,
  LockIcon,
  primaryButtonClass,
  secondaryButtonClass,
} from './deedUi'
import { descriptionError, fromBeneficiariesCount, toBeneficiariesCount } from './formValues'
import { PeopleStepper } from './PeopleStepper'
import { visibleDoerName } from './deedText'

const NOT_EDITABLE = 'This report can’t be edited any more.'

/**
 * `/me/deeds/:id/edit` (F6 canvas): category, description and people helped. Who, when and
 * where are shown locked: they are the verification evidence. `PATCH` sends only the changed
 * fields. No photo section in M2 (D23).
 */
export function EditDeedPage() {
  const { id = '' } = useParams()
  const options = useDeedOptions()
  const query = useQuery({
    queryKey: deedsKeys.deed(id),
    queryFn: () => deedsApi.deed(id),
    retry: false,
  })
  const detail = `/me/deeds/${encodeURIComponent(id)}`

  if (query.isPending || options.isPending) return <Loading />
  if (query.isError || options.isError) {
    const error = query.error ?? options.error
    return (
      <F6Main>
        <BackLink to={detail} label="Cancel" />
        <ErrorAlert>
          {error instanceof ApiError && error.status === 404
            ? 'This deed isn’t available.'
            : errorMessage(error)}
        </ErrorAlert>
      </F6Main>
    )
  }
  if (!query.data.can_edit) {
    return (
      <F6Main>
        <BackLink to={detail} label="My deeds" />
        <ErrorAlert>{NOT_EDITABLE}</ErrorAlert>
        <Link to={detail} className={`${secondaryButtonClass} self-start`}>
          Back to the report
        </Link>
      </F6Main>
    )
  }
  return <EditForm deed={query.data} options={options.data} detail={detail} />
}

function EditForm({
  deed,
  options,
  detail,
}: {
  deed: DeedOut
  options: NonNullable<ReturnType<typeof useDeedOptions>['data']>
  detail: string
}) {
  const navigate = useNavigate()
  const invalidate = useInvalidateDeeds()
  const [category, setCategory] = useState(deed.category)
  const [description, setDescription] = useState(deed.description)
  const [people, setPeople] = useState(fromBeneficiariesCount(deed.beneficiaries_count))
  const [showErrors, setShowErrors] = useState(false)

  const save = useMutation({
    mutationFn: (changes: DeedUpdate) => deedsApi.updateDeed(deed.id, changes),
    onSuccess: (updated) => {
      invalidate(updated)
      navigate(detail, { replace: true })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'deed_not_editable') {
        invalidate()
        navigate(detail, {
          replace: true,
          state: { notice: NOT_EDITABLE } satisfies DeedDetailState,
        })
      }
    },
  })

  const descError = showErrors ? descriptionError(description, options.description_min) : null

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (descriptionError(description, options.description_min)) {
      setShowErrors(true)
      return
    }
    const changes: DeedUpdate = {}
    if (category !== deed.category) changes.category = category
    if (description.trim() !== deed.description) changes.description = description.trim()
    const count = toBeneficiariesCount(people)
    if (count !== deed.beneficiaries_count) changes.beneficiaries_count = count
    if (Object.keys(changes).length === 0) {
      navigate(detail, { replace: true })
      return
    }
    save.mutate(changes)
  }

  const formError =
    save.error && !(save.error instanceof ApiError && save.error.code === 'deed_not_editable')
      ? errorMessage(save.error)
      : null

  return (
    <F6Main>
      <BackLink to={detail} label="Cancel" />
      <F6Title>Edit your report</F6Title>
      <section
        aria-labelledby="locked-heading"
        className="flex flex-col gap-2.5 rounded-2xl bg-slate-100 px-4 py-3.5"
      >
        <h2
          id="locked-heading"
          className="flex items-center gap-2 text-[15px] font-bold text-slate-700"
        >
          <LockIcon />
          Can’t be changed
        </h2>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[15px]">
          <dt className="text-slate-600">Who</dt>
          <dd className="font-semibold">{visibleDoerName(deed) ?? 'Not yet known'}</dd>
          <dt className="text-slate-600">When</dt>
          <dd className="font-semibold">{formatOccurredAt(deed.occurred_at)}</dd>
          <dt className="text-slate-600">Where</dt>
          <dd className="font-semibold">
            {deed.verification_method === 'qr_scan' ? 'Checked by QR and location' : 'Invite link'}
          </dd>
        </dl>
        <p className="text-sm text-slate-600">These are the proof that you were there.</p>
      </section>
      <form noValidate onSubmit={onSubmit} className="flex flex-col gap-6">
        <CategoryPills categories={options.categories} value={category} onChange={setCategory} />
        <DescriptionField
          value={description}
          onChange={setDescription}
          max={options.description_max}
          error={descError}
        />
        <PeopleStepper value={people} onChange={setPeople} max={options.beneficiaries_max} />
        <p className="text-sm text-[#586579]">
          Changes are saved with a history, and the deed shows “Edited”.
        </p>
        {formError && <ErrorAlert>{formError}</ErrorAlert>}
        <div className="flex flex-col gap-2.5">
          <button
            type="submit"
            disabled={save.isPending}
            className={`${primaryButtonClass} min-h-13 text-[17px]`}
          >
            {save.isPending ? 'Saving…' : 'Save changes'}
          </button>
          <Link to={detail} className={secondaryButtonClass}>
            Cancel
          </Link>
        </div>
      </form>
    </F6Main>
  )
}
