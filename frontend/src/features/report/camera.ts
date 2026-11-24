/** Feature checks for in-app QR scanning (M2-DESIGN §5.3 Scan). */

/** The part of the (not yet standard in TypeScript's DOM types) BarcodeDetector we use. */
export type BarcodeDetectorLike = {
  detect: (source: CanvasImageSource) => Promise<Array<{ rawValue: string }>>
}

export function hasBarcodeDetector(): boolean {
  return typeof window !== 'undefined' && 'BarcodeDetector' in window
}

/** In-app scanning needs both the detector and a camera API. */
export function canScanInApp(): boolean {
  return (
    hasBarcodeDetector() &&
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices?.getUserMedia === 'function'
  )
}

/** A mouse-and-keyboard computer, for the "On a computer?" note on the camera-app screen. */
export function isDesktopPointer(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(hover: hover) and (pointer: fine)').matches
}
