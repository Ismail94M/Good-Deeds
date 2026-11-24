import type { FormEvent, ReactNode } from 'react'
import { CategoryPills } from '../deeds/CategoryPills'
import type { DeedOptions, ScanDetail } from '../deeds/deedsApi'
import { DescriptionField } from '../deeds/DescriptionField'
import { descriptionHint } from '../deeds/formValues'
import { PeopleStepper } from '../deeds/PeopleStepper'
import { firstName, formatMinutes, initials } from './format'
import { pairLimitNotice } from './limits'
import {
  Banner,
  CheckIcon,
  focusRing,
  inputBorder,
  LimitBanner,
  primaryButton,
  WarningIcon,
} from './reportUi'
import { EARLIER_CHOICES, validateDeedForm, type DeedFormValues } from './deedFormValues'

/**
 * The deed form on `/report/scan/:scanId` (F3 canvas, row 4): who, the send-within countdown,
 * the pair warning, category, description, people helped, when, the public-feed notice (D22)
 * the photos section (M3, only when `/media/options` has `photos_enabled`; the page owns the
 * uploads) and Send. The values live in the page, so they survive an error screen.
 */
export function DeedForm({
  scan,
  options,
  secondsLeft,
  values,
  onChange,
  showErrors,
  formError,
  sending,
  uploadingPhotos = false,
  photos,
  onSubmit,
}: {
  scan: ScanDetail
  options: DeedOptions
  secondsLeft: number
  values: DeedFormValues
  onChange: (patch: Partial<DeedFormValues>) => void
  showErrors: boolean
  formError: string | null
  sending: boolean
  /** Send was pressed while photos were still uploading: it waits for them (§6.3). */
  uploadingPhotos?: boolean
  /** The photo section (M3-DESIGN §6.3), or nothing when photos are off. */
  photos?: ReactNode
  onSubmit: () => void
}) {
  const doer = scan.doer
  const first = firstName(doer.display_name)
  const pairNotice = pairLimitNotice(scan, options.reporter_doer_monthly_warn_at)
  const errors = showErrors ? validateDeedForm(values, options) : {}
  const earlierChoices = EARLIER_CHOICES.filter(
    (choice) => choice.minutes <= options.qr_occurred_max_age_minutes,
  )
  const mm = Math.floor(secondsLeft / 60)
  const ss = String(secondsLeft % 60).padStart(2, '0')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSubmit()
  }

  return (
    <>
      <section
        aria-label="Who you’re recognising"
        className="flex items-center gap-3.5 rounded-2xl border border-slate-200 bg-white p-4"
      >
        <span
          aria-hidden
          className="flex size-13 shrink-0 items-center justify-center rounded-full bg-brand-100 text-lg font-bold text-brand-900"
        >
          {initials(doer.display_name)}
        </span>
        <div className="min-w-0 grow">
          <p className="text-[13px] font-semibold tracking-[0.04em] text-slate-600 uppercase">
            Recognising
          </p>
          <p className="text-[19px] leading-tight font-bold break-words">{doer.display_name}</p>
          <p className="text-sm break-words text-[#586579]">@{doer.handle}</p>
          {scan.area_name && (
            <p className="text-sm break-words text-emerald-700">
              Verified just now · {scan.area_name}
            </p>
          )}
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[13px] font-semibold text-emerald-700">
          <CheckIcon className="size-3.5" />
          Matched
        </span>
      </section>

      <div
        role="timer"
        aria-live="off"
        className="flex items-center gap-2.5 rounded-xl bg-brand-50 px-3.5 py-2.5 text-sm text-brand-900"
      >
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="size-4.5 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
        <span>
          <strong>
            Send within {mm}:{ss}.
          </strong>{' '}
          A scan stays valid for {options.scan_submit_minutes} minutes.
        </span>
      </div>

      {pairNotice && <LimitBanner notice={pairNotice} />}

      <form
        aria-label="Describe the deed"
        noValidate
        onSubmit={submit}
        className="flex flex-col gap-6"
      >
        <CategoryPills
          categories={options.categories}
          value={values.category}
          onChange={(category) => onChange({ category })}
          error={errors.category}
        />

        <DescriptionField
          value={values.description}
          onChange={(description) => onChange({ description })}
          max={options.description_max}
          hint={descriptionHint(options.description_min)}
          error={errors.description}
        />

        <PeopleStepper
          value={values.people}
          onChange={(people) => onChange({ people })}
          max={options.beneficiaries_max}
        />

        <fieldset className="min-w-0">
          <legend className="mb-2.5 font-semibold text-slate-900">When did it happen?</legend>
          <div className="flex flex-wrap gap-2">
            <WhenPill selected={values.when === 'now'} onClick={() => onChange({ when: 'now' })}>
              Just now
            </WhenPill>
            {earlierChoices.length > 0 && (
              <WhenPill
                selected={values.when === 'earlier'}
                onClick={() => onChange({ when: 'earlier' })}
              >
                Earlier
              </WhenPill>
            )}
          </div>
          {values.when === 'earlier' && earlierChoices.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5">
              <label htmlFor="when-earlier" className="text-[15px] font-medium">
                How long before you scanned?
              </label>
              <select
                id="when-earlier"
                value={values.earlierMinutes}
                onChange={(e) => onChange({ earlierMinutes: Number(e.target.value) })}
                className={`min-h-12 w-full rounded-lg bg-white px-3 text-base text-slate-900 ${inputBorder} ${focusRing}`}
              >
                {earlierChoices.map((choice) => (
                  <option key={choice.minutes} value={choice.minutes}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <p className="mt-2 text-sm text-[#586579]">
            Up to {formatMinutes(options.qr_occurred_max_age_minutes)} before you scanned.
          </p>
        </fieldset>

        {photos}

        <div className="flex items-start gap-2.5 rounded-xl bg-brand-50 px-3.5 py-3 text-sm text-brand-900">
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            className="mt-px size-5 shrink-0"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
          <p>
            <strong>Verified deeds appear in the public feed.</strong> You or {first} can hide it at
            any time. Only the area name is shown, never your exact location.
          </p>
        </div>

        {formError && (
          <Banner tone="danger" role="alert" icon={<WarningIcon />}>
            {formError}
          </Banner>
        )}

        <div className="flex flex-col gap-2.5">
          <button
            type="submit"
            disabled={sending || uploadingPhotos}
            className={`${primaryButton} min-h-13 text-[17px]`}
          >
            {uploadingPhotos ? 'Uploading photos…' : sending ? 'Sending…' : 'Send report'}
          </button>
          <p className="text-center text-sm text-[#586579]">
            It goes on {first}’s profile as soon as you send it.
          </p>
        </div>
      </form>
    </>
  )
}

function WhenPill({
  selected,
  onClick,
  children,
}: {
  selected: boolean
  onClick: () => void
  children: string
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`min-h-11 rounded-full border px-4 text-[15px] transition-colors ${focusRing} ${
        selected
          ? 'border-brand-700 bg-brand-700 font-semibold text-white'
          : 'border-slate-300 bg-white font-medium text-slate-800 hover:bg-slate-50'
      }`}
    >
      {children}
    </button>
  )
}
