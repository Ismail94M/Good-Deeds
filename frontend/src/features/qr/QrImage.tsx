import { useQuery } from '@tanstack/react-query'
import QRCode from 'qrcode'

/** Renders `value` as an SVG QR code. */
export function QrImage({ value, label }: { value: string; label: string }) {
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
      className="mx-auto aspect-square w-full max-w-80 rounded-2xl bg-white p-2 shadow-sm ring-1 ring-stone-200 [&>svg]:size-full"
      // The SVG comes from the qrcode library, not from user input.
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    >
      {svg ? undefined : (
        <span className="flex size-full items-center justify-center text-stone-500">
          {isError ? 'Could not draw the QR code.' : 'Drawing your QR…'}
        </span>
      )}
    </div>
  )
}
