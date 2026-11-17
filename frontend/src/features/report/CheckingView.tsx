import { ReportMain } from './reportUi'

/** "Checking…" while `POST /api/scans` runs (F3 canvas, M-Checking). */
export function CheckingView() {
  return (
    <ReportMain>
      <div
        role="status"
        aria-live="polite"
        className="flex flex-col items-center gap-4.5 pt-20 pb-10 text-center"
      >
        <svg aria-hidden viewBox="0 0 48 48" className="size-14 animate-spin">
          <circle cx="24" cy="24" r="20" fill="none" stroke="#dfe6fc" strokeWidth="5" />
          <path
            d="M24 4a20 20 0 0 1 20 20"
            fill="none"
            stroke="#3659e3"
            strokeWidth="5"
            strokeLinecap="round"
          />
        </svg>
        <h1 className="text-[22px] font-bold text-slate-900">Checking…</h1>
        <p className="max-w-[300px] text-slate-600">
          Matching the code and making sure you’re both in the same place.
        </p>
      </div>
    </ReportMain>
  )
}
