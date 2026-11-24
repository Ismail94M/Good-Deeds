import { useEffect, useRef } from 'react'
import { hasBarcodeDetector, type BarcodeDetectorLike } from './camera'

/** How often a video frame is checked for a QR code. */
const SCAN_INTERVAL_MS = 250

/**
 * The rear camera with `BarcodeDetector` polling its frames (M2-DESIGN §5.1). Calls `onCode`
 * with each QR value it reads, and `onUnavailable` when the camera can't be opened (no camera,
 * permission refused, no detector). Every camera track is stopped on unmount.
 */
export function QrCamera({
  onCode,
  onUnavailable,
}: {
  onCode: (raw: string) => void
  onUnavailable: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const onCodeRef = useRef(onCode)
  const onUnavailableRef = useRef(onUnavailable)
  useEffect(() => {
    onCodeRef.current = onCode
    onUnavailableRef.current = onUnavailable
  })

  useEffect(() => {
    let cancelled = false
    let stream: MediaStream | null = null
    let timer: number | undefined
    let busy = false

    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer)
      stream?.getTracks().forEach((track) => track.stop())
      stream = null
    }

    async function start() {
      if (!hasBarcodeDetector() || !navigator.mediaDevices?.getUserMedia) {
        onUnavailableRef.current()
        return
      }
      let detector: BarcodeDetectorLike
      try {
        const Detector = (window as unknown as { BarcodeDetector: BarcodeDetectorCtor })
          .BarcodeDetector
        detector = new Detector({ formats: ['qr_code'] })
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment' },
          audio: false,
        })
      } catch {
        if (!cancelled) onUnavailableRef.current()
        return
      }
      if (cancelled) {
        stop()
        return
      }
      const video = videoRef.current
      if (!video) return
      video.srcObject = stream
      try {
        // Autoplay of a muted inline video is allowed; a rejected play() is ignored.
        void Promise.resolve(video.play()).catch(() => undefined)
      } catch {
        // Some browsers throw instead of rejecting.
      }
      timer = window.setInterval(() => {
        if (busy || video.readyState < 2) return
        busy = true
        detector
          .detect(video)
          .then((codes) => {
            if (cancelled) return
            for (const code of codes) {
              if (code.rawValue) {
                onCodeRef.current(code.rawValue)
                break
              }
            }
          })
          .catch(() => {
            // A frame that can't be read is skipped.
          })
          .finally(() => {
            busy = false
          })
      }, SCAN_INTERVAL_MS)
    }

    void start()
    return () => {
      cancelled = true
      stop()
    }
  }, [])

  return (
    <video
      ref={videoRef}
      muted
      playsInline
      aria-hidden
      className="absolute inset-0 size-full rounded-3xl object-cover"
    />
  )
}

type BarcodeDetectorCtor = new (options: { formats: string[] }) => BarcodeDetectorLike
