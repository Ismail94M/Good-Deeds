import { useQuery } from '@tanstack/react-query'
import QRCode from 'qrcode'

/** Renders `value` as an SVG QR code. `className` sets the size and frame. */
export function QrImage({
  value,
  label,
  className = 'mx-auto w-full max-w-80 rounded-2xl p-2 shadow-sm ring-1 ring-slate-200',
}: {
  value: string
  label: string
  className?: string
}) {
  const { data: svg, isError } = useQuery({
    queryKey: ['qr-svg', value],
    queryFn: () => QRCode.toString(value, { type: 'svg', margin: 2, errorCorrectionLevel: 'M' }),
    staleTime: Infinity,
    gcTime: 60_000,
  })

  return (
    <div
      role="img"
      aria-label={label}
      className={`aspect-square bg-white [&>svg]:size-full ${className}`}
      // The SVG comes from the qrcode library, not from user input.
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    >
      {svg ? undefined : (
        <span className="flex size-full items-center justify-center text-center text-sm text-slate-500">
          {isError ? 'Could not draw the QR code.' : 'Drawing your QR…'}
        </span>
      )}
    </div>
  )
}
