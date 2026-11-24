/** Seconds at or below which the countdown turns orange (F2 canvas: the last 5 s). */
export const ENDING_AT = 5

/**
 * "New code in {n} s" with a bar that empties to 0 (F2 canvas). Full length is one window;
 * blue, then orange for the last 5 s. `size="large"` for the large-QR view.
 */
export function CountdownBar({
  secondsLeft,
  windowSeconds,
  size = 'normal',
}: {
  secondsLeft: number
  windowSeconds: number
  size?: 'normal' | 'large'
}) {
  const ending = secondsLeft <= ENDING_AT
  const pct = Math.max(0, Math.min(100, Math.round((secondsLeft / windowSeconds) * 100)))
  return (
    <div className="flex w-full flex-col gap-2">
      <div
        className={`flex items-baseline gap-2 ${
          size === 'large' ? 'justify-center text-base' : 'justify-between text-sm'
        }`}
      >
        <span
          data-ending={ending || undefined}
          className={`font-semibold ${ending ? 'text-orange-700' : 'text-brand-700'}`}
        >
          New code in {secondsLeft} s
        </span>
        {size === 'normal' && (
          <span className="text-[#586579]">Changes every {windowSeconds} s</span>
        )}
      </div>
      <div
        role="progressbar"
        aria-label="Time until the code changes"
        aria-valuemin={0}
        aria-valuemax={windowSeconds}
        aria-valuenow={secondsLeft}
        className={`overflow-hidden rounded-full bg-brand-100 ${size === 'large' ? 'h-2.5' : 'h-2'}`}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-1000 ease-linear ${
            ending ? 'bg-orange-600' : 'bg-brand-700'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
