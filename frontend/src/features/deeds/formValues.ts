/** Values and validation shared by the deed, edit and self-log forms (M2-DESIGN §5.3). */

/** The deed form's help line (F3 canvas). Edit and self-log forms show none. */
export function descriptionHint(min: number): string {
  return `At least ${min} characters. Please leave out phone numbers and addresses.`
}

/** The message shown on Send when the description is too short, or null when it is fine. */
export function descriptionError(value: string, min: number): string | null {
  return value.trim().length < min
    ? `Write at least ${min} characters so people understand what happened.`
    : null
}

/** The new-deed default: one person helped. */
export const PEOPLE_DEFAULT = 1

/** The API has no "0 people": 0 means "not given" and is sent as `null` (§5.3). */
export function toBeneficiariesCount(people: number): number | null {
  return people > 0 ? people : null
}

/** The stepper value for a stored count (`null`, not given, shows as 0). */
export function fromBeneficiariesCount(count: number | null): number {
  return count ?? 0
}
