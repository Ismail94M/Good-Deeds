/** Window length when the API doesn't send `window_seconds` (older backend). */
export const DEFAULT_WINDOW_SECONDS = 30

/** "482 913" from "482913", for reading out loud. */
export function formatManualCode(code: string): string {
  return code.length === 6 ? `${code.slice(0, 3)} ${code.slice(3)}` : code
}

/**
 * Q21 (PO 2026-11-17): the doer is told deeds about them become public. Shown on My QR and in
 * the Privacy Notice.
 */
export const PUBLIC_FEED_NOTICE =
  'When someone records a deed you did, it appears in the public Good Deeds feed with your name and the area. You can hide any deed from your profile at any time.'
