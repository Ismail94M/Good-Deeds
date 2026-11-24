/**
 * Browser-side resize before upload (docs/M3-DESIGN.md §4.4 step 5): decode with the EXIF
 * orientation applied, scale to at most 2048 px on the long side, flatten onto white and
 * re-encode as JPEG 0.85. Re-encoding drops all metadata (GPS, camera, time) and saves mobile
 * data. This is defence in depth, not a control: the worker strips everything again.
 *
 * The result is the "displayed, orientation-corrected" frame the blur regions refer to (§4.5).
 */

export const MAX_SIDE = 2048
export const JPEG_QUALITY = 0.85
export const OUTPUT_TYPE = 'image/jpeg'

export type ResizedImage = { blob: Blob; width: number; height: number }

/** The file couldn't be decoded here (not an image, or a format this browser can't read). */
export class ImageUnreadable extends Error {
  constructor() {
    super('This image could not be read.')
    this.name = 'ImageUnreadable'
  }
}

/** Scale `width × height` down (never up) so the long side is at most `maxSide`. */
export function fitWithin(
  width: number,
  height: number,
  maxSide: number = MAX_SIDE,
): { width: number; height: number } {
  const longest = Math.max(width, height)
  if (longest <= maxSide) return { width, height }
  const scale = maxSide / longest
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  }
}

type Source = { image: CanvasImageSource; width: number; height: number; close: () => void }

async function decode(file: Blob): Promise<Source> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
      return {
        image: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        close: () => bitmap.close(),
      }
    } catch {
      // Fall through to <img>, which some browsers decode more formats with.
    }
  }
  // <img> applies EXIF orientation by default (`image-orientation: from-image`).
  const url = URL.createObjectURL(file)
  try {
    const img = new Image()
    img.decoding = 'async'
    img.src = url
    await img.decode()
    return { image: img, width: img.naturalWidth, height: img.naturalHeight, close: () => {} }
  } catch {
    throw new ImageUnreadable()
  } finally {
    URL.revokeObjectURL(url)
  }
}

export async function resizeImage(file: Blob, maxSide: number = MAX_SIDE): Promise<ResizedImage> {
  const source = await decode(file)
  try {
    if (source.width < 1 || source.height < 1) throw new ImageUnreadable()
    const { width, height } = fitWithin(source.width, source.height, maxSide)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new ImageUnreadable()
    // JPEG has no alpha: flatten transparent PNG/WebP onto white, as the worker does.
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, width, height)
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(source.image, 0, 0, width, height)
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, OUTPUT_TYPE, JPEG_QUALITY),
    )
    if (!blob) throw new ImageUnreadable()
    return { blob, width, height }
  } finally {
    source.close()
  }
}
