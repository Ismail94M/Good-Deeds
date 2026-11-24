/** Test data for the photo screens (used only by `*.test.tsx`). */

import { vi } from 'vitest'
import type { MediaOptions, OwnerPhoto } from './mediaApi'

export function makeMediaOptions(overrides: Partial<MediaOptions> = {}): MediaOptions {
  return {
    photos_enabled: true,
    photos_per_owner: 4,
    photo_edit_window_hours: 24,
    upload_allowed_types: ['image/jpeg', 'image/png', 'image/webp'],
    upload_max_bytes: 10 * 1024 * 1024,
    blur_max_regions: 8,
    decision_note_max: 1000,
    report_reasons: [
      { id: 'not_consented', label: 'I’m in this photo and didn’t agree', priority: 2 },
      {
        id: 'child_no_guardian_consent',
        label: 'A child is shown without a parent’s consent',
        priority: 1,
      },
      {
        id: 'private_details',
        label: 'It shows private details (like a phone number or address)',
        priority: 2,
      },
      { id: 'inappropriate', label: 'It’s inappropriate or harmful', priority: 2 },
      { id: 'other', label: 'Something else', priority: 3 },
    ],
    ...overrides,
  }
}

export function makeOwnerPhoto(overrides: Partial<OwnerPhoto> = {}): OwnerPhoto {
  const id = overrides.id ?? 'p1'
  return {
    id,
    position: 1,
    status: 'ready',
    thumb_url: `https://storage.test/photos/${id}/thumb.webp?sig=1`,
    display_url: `https://storage.test/photos/${id}/display.webp?sig=1`,
    blurred_count: 0,
    can_remove: true,
    ...overrides,
  }
}

/** A JPEG-typed file of `size` bytes. */
export function jpeg(name = 'photo.jpg', size = 2048): File {
  return new File([new Uint8Array(size)], name, { type: 'image/jpeg' })
}

/** The resized blob the mocked `resizeImage` returns (its size is the signed length). */
export const RESIZED_BYTES = 1234

/**
 * Stand-ins for the browser image APIs jsdom lacks. Call from a test file's top level with
 * `vi.mock('./resizeImage', ...)` instead when the module itself is under test.
 */
export function stubObjectUrls() {
  let n = 0
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:preview-${++n}`)
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
}
