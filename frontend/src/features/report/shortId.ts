/**
 * Client-side normalisation for the typed ID and code (M2-DESIGN §5.3 "Capitals and spaces
 * don't matter"). Mirrors `identity._qr.normalize_short_id`: Crockford base32, 7 characters,
 * an optional "GD" prefix, O/I/L read as 0/1/1.
 */

const ALPHABET = /^[0-9ABCDEFGHJKMNPQRSTVWXYZ]{7}$/
const LOOKALIKES: Record<string, string> = { O: '0', I: '1', L: '1' }

/** "GD-7K3M9Q0" from "gd 7k3m 9q0", "GD7K3M9Q0" or "7k3m9q0"; null when malformed. */
export function normalizeShortId(raw: string): string | null {
  let value = raw.trim().toUpperCase().replace(/[\s-]/g, '')
  if (value.length === 9 && value.startsWith('GD')) value = value.slice(2)
  value = value.replace(/[OIL]/g, (c) => LOOKALIKES[c])
  return ALPHABET.test(value) ? `GD-${value}` : null
}

/** "482913" from "482 913"; null unless exactly six digits remain. */
export function normalizeCode(raw: string): string | null {
  const value = raw.replace(/\s/g, '')
  return /^\d{6}$/.test(value) ? value : null
}
