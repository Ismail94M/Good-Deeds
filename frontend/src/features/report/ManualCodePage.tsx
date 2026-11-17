import { useQuery } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { FieldError } from '../../components/ui'
import { deedsApi, deedsKeys } from '../deeds/deedsApi'
import { CheckingView } from './CheckingView'
import { copyFor, errorCopy } from './errorCopy'
import { LocationGate } from './LocationGate'
import type { LocationGateApi } from './reportContext'
import { ReportErrorView } from './ReportErrorView'
import {
  AlertIcon,
  BackButton,
  Banner,
  focusRing,
  LocationChip,
  primaryButton,
  ReportMain,
  ScreenTitle,
} from './reportUi'
import { normalizeCode, normalizeShortId } from './shortId'
import { useScanSubmit } from './useScanSubmit'

/** `/report/code`: the location gate, then "Enter their ID and code" (F3 canvas). */
export function ManualCodePage() {
  return <LocationGate>{(gate) => <ManualCodeForm gate={gate} />}</LocationGate>
}

const ID_INVALID = 'Check their ID. It is GD- followed by 7 letters and numbers.'
const CODE_INVALID = 'Enter the 6-digit code from their screen.'

function ManualCodeForm({ gate }: { gate: LocationGateApi }) {
  const navigate = useNavigate()
  const options = useQuery({ queryKey: deedsKeys.options, queryFn: deedsApi.options })
  const { state, submit, reset } = useScanSubmit(gate)
  const [shortId, setShortId] = useState('')
  const [code, setCode] = useState('')
  const [fieldErrors, setFieldErrors] = useState<{ id?: string; code?: string }>({})
  const idFieldId = useId()
  const codeFieldId = useId()

  if (state.status === 'checking') return <CheckingView />
  if (state.status === 'error') {
    return (
      <ReportErrorView
        copy={errorCopy(state.error, {
          options: options.data,
          accuracyM: gate.location.accuracy_m,
        })}
        onRetry={reset}
        onScanAgain={() => navigate('/report/scan')}
      />
    )
  }

  const mismatch = state.status === 'code_mismatch'
  const mismatchCopy = copyFor('doer_code_invalid')

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const normalizedId = normalizeShortId(shortId)
    const normalizedCode = normalizeCode(code)
    const errors = {
      id: normalizedId ? undefined : ID_INVALID,
      code: normalizedCode ? undefined : CODE_INVALID,
    }
    setFieldErrors(errors)
    if (!normalizedId || !normalizedCode) {
      document.getElementById(normalizedId ? codeFieldId : idFieldId)?.focus()
      return
    }
    setShortId(normalizedId)
    void submit({ short_id: normalizedId, code: normalizedCode })
  }

  const codeOutlined = mismatch || !!fieldErrors.code
  const mono =
    "block w-full rounded-lg bg-white px-3.5 font-['JetBrains_Mono',ui-monospace,monospace] text-slate-900"

  return (
    <ReportMain>
      <BackButton onClick={() => navigate('/report')} />
      <form noValidate onSubmit={onSubmit} className="flex flex-col gap-4.5">
        <div className="flex flex-col gap-1.5">
          <ScreenTitle>Enter their ID and code</ScreenTitle>
          <p className="text-slate-600">
            Ask them to open <strong>My QR</strong> in Good Deeds and read out both.
          </p>
        </div>
        {mismatch && (
          <Banner tone="danger" role="alert" icon={<AlertIcon />}>
            <strong className="block">{mismatchCopy.title}.</strong>
            {mismatchCopy.body}
          </Banner>
        )}
        <div className="flex flex-col gap-1.5">
          <label htmlFor={idFieldId} className="font-semibold">
            Their ID
          </label>
          <input
            id={idFieldId}
            type="text"
            value={shortId}
            onChange={(e) => setShortId(e.target.value)}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            aria-invalid={fieldErrors.id ? true : undefined}
            aria-describedby={`${idFieldId}-hint${fieldErrors.id ? ` ${idFieldId}-error` : ''}`}
            className={`${mono} min-h-13 text-xl font-semibold tracking-[0.04em] ${focusRing} ${
              fieldErrors.id ? 'border-2 border-red-700' : 'border border-slate-300'
            }`}
          />
          <span id={`${idFieldId}-hint`} className="text-sm text-[#586579]">
            It starts with GD-. Capitals and spaces don’t matter.
          </span>
          {fieldErrors.id && <FieldError id={`${idFieldId}-error`}>{fieldErrors.id}</FieldError>}
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={codeFieldId} className="font-semibold">
            6-digit code
          </label>
          <input
            id={codeFieldId}
            type="text"
            inputMode="numeric"
            autoComplete="off"
            maxLength={7}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-invalid={codeOutlined ? true : undefined}
            aria-describedby={`${codeFieldId}-hint${fieldErrors.code ? ` ${codeFieldId}-error` : ''}`}
            className={`${mono} min-h-14 text-[28px] font-bold tracking-[0.18em] ${focusRing} ${
              codeOutlined ? 'border-2 border-red-700' : 'border border-slate-300'
            }`}
          />
          <span id={`${codeFieldId}-hint`} className="text-sm text-[#586579]">
            It changes every 30 seconds, so type it straight away.
          </span>
          {fieldErrors.code && (
            <FieldError id={`${codeFieldId}-error`}>{fieldErrors.code}</FieldError>
          )}
        </div>
        <LocationChip accuracyM={gate.location.accuracy_m} />
        <button type="submit" className={primaryButton}>
          Continue
        </button>
      </form>
    </ReportMain>
  )
}
