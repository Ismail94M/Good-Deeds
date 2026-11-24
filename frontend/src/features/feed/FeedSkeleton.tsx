const bar = 'rounded-md bg-[#eef1f5]'

/** Two placeholder cards while the feed loads (F7 "Feed loading"). */
export function FeedSkeleton({ label = 'Loading the feed' }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 motion-safe:animate-pulse">
        <div className="flex items-center gap-3">
          <span className="size-11 rounded-full bg-[#eef1f5]" />
          <span className="flex grow flex-col gap-1.5">
            <span className={`h-3.5 w-[55%] ${bar}`} />
            <span className={`h-3 w-[35%] ${bar}`} />
          </span>
        </div>
        <div className={`h-55 rounded-xl bg-[#eef1f5]`} />
        <div className={`h-3.5 w-[85%] ${bar}`} />
      </div>
      <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 motion-safe:animate-pulse">
        <div className="flex items-center gap-3">
          <span className="size-11 rounded-full bg-[#eef1f5]" />
          <span className="flex grow flex-col gap-1.5">
            <span className={`h-3.5 w-[45%] ${bar}`} />
            <span className={`h-3 w-[30%] ${bar}`} />
          </span>
        </div>
        <div className={`h-3.5 w-[90%] ${bar}`} />
      </div>
    </div>
  )
}
