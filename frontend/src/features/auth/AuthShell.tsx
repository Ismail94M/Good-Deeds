import type { ReactNode } from 'react'
import { LogoMark } from '../../components/ui'

/**
 * Illustrated frame for the sign-in and sign-up pages (Figma "Account" screen).
 * The illustrations live in /public/auth; replace them with full-size exports
 * from Figma using the same file names.
 */
const TILES = [
  { src: '/auth/street.webp', className: 'left-0 top-10 w-56 h-64 -rotate-1' },
  { src: '/auth/beach.webp', className: 'left-10 top-[19rem] w-52 h-44 rotate-1' },
  { src: '/auth/planting.webp', className: 'right-4 top-16 w-52 h-52 rotate-1' },
  { src: '/auth/community.webp', className: 'right-28 top-[19rem] w-40 h-56 -rotate-1' },
]

export function AuthShell({
  title,
  intro,
  children,
}: {
  title: string
  intro: ReactNode
  children: ReactNode
}) {
  return (
    <main className="flex flex-1 items-start justify-center px-4 py-6 sm:py-10 lg:px-6">
      <div className="relative w-full max-w-6xl lg:min-h-[40rem] lg:rounded-3xl lg:border lg:border-white lg:bg-gradient-to-br lg:from-white/70 lg:via-brand-50/40 lg:to-amber-50/50 lg:py-12 lg:shadow-sm">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 hidden overflow-hidden rounded-3xl lg:block"
        >
          {TILES.map((tile) => (
            <img
              key={tile.src}
              src={tile.src}
              alt=""
              className={`absolute rounded-2xl object-cover shadow-lg ring-4 ring-white/80 ${tile.className}`}
            />
          ))}
        </div>
        <section className="relative mx-auto w-full max-w-md space-y-6 rounded-2xl border border-slate-100 bg-white p-6 shadow-xl shadow-slate-200/60 sm:p-8">
          <header className="space-y-3">
            <LogoMark className="size-12" />
            <h1 className="text-2xl font-bold tracking-tight text-balance text-slate-900 sm:text-3xl">
              {title}
            </h1>
            <p className="text-slate-500">{intro}</p>
          </header>
          {children}
          <p className="flex items-center justify-center gap-1.5 text-xs text-slate-400">
            <LockIcon />
            Your connection is secure and encrypted.
          </p>
        </section>
      </div>
    </main>
  )
}

function LockIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-3.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  )
}
