import { describe, expect, it } from 'vitest'
import { INITIAL_DEED_FORM, toQrDeedRequest, validateDeedForm } from './deedFormValues'
import { formatMinutes, formatOccurredAt, initials } from './format'
import { parseScanUrl } from './parseScanUrl'
import { normalizeCode, normalizeShortId } from './shortId'

const ORIGIN = 'https://gooddeeds.example'

describe('parseScanUrl', () => {
  it('returns the token of a same-origin /scan/:token URL', () => {
    expect(parseScanUrl(`${ORIGIN}/scan/7K3M9Q0.123.abc`, ORIGIN)).toBe('7K3M9Q0.123.abc')
    expect(parseScanUrl(`${ORIGIN}/scan/7K3M9Q0.123.abc/`, ORIGIN)).toBe('7K3M9Q0.123.abc')
    expect(parseScanUrl(`  ${ORIGIN}/scan/a%2Eb  `, ORIGIN)).toBe('a.b')
  })

  it('defaults to this page’s origin', () => {
    expect(parseScanUrl(`${window.location.origin}/scan/tok`)).toBe('tok')
  })

  it.each([
    ['another site', 'https://evil.example/scan/tok'],
    ['another scheme', 'http://gooddeeds.example/scan/tok'],
    ['another port', 'https://gooddeeds.example:8443/scan/tok'],
    ['another path', `${ORIGIN}/u/ahmed`],
    ['a nested path', `${ORIGIN}/scan/tok/extra`],
    ['no token', `${ORIGIN}/scan/`],
    ['plain text', 'hello world'],
    ['a bad escape', `${ORIGIN}/scan/%E0%A4%A`],
  ])('ignores %s', (_label, raw) => {
    expect(parseScanUrl(raw, ORIGIN)).toBeNull()
  })
})

describe('normalizeShortId', () => {
  it.each([
    ['GD-7K3M9Q0', 'GD-7K3M9Q0'],
    ['gd-7k3m9q0', 'GD-7K3M9Q0'],
    ['gd 7k3m 9q0', 'GD-7K3M9Q0'],
    ['GD7K3M9Q0', 'GD-7K3M9Q0'],
    ['7k3m9q0', 'GD-7K3M9Q0'],
    ['GD-7K3M9QO', 'GD-7K3M9Q0'],
    ['GD-IL3M9Q0', 'GD-113M9Q0'],
  ])('%s → %s', (raw, expected) => {
    expect(normalizeShortId(raw)).toBe(expected)
  })

  it.each(['', 'GD-', 'GD-7K3M9Q', 'GD-7K3M9Q00', 'GD-7K3M9QU'])('rejects %j', (raw) => {
    expect(normalizeShortId(raw)).toBeNull()
  })
})

describe('normalizeCode', () => {
  it('drops spaces and needs six digits', () => {
    expect(normalizeCode('482 913')).toBe('482913')
    expect(normalizeCode(' 482913 ')).toBe('482913')
    expect(normalizeCode('48291')).toBeNull()
    expect(normalizeCode('48291a')).toBeNull()
  })
})

describe('deed form values', () => {
  const scan = { scan_id: 's1', scanned_at: '2026-11-17T10:00:00.000Z' }

  it('requires a category and a long enough description', () => {
    expect(validateDeedForm(INITIAL_DEED_FORM, { description_min: 10 })).toEqual({
      category: 'Choose what kind of deed it was.',
      description: 'Write at least 10 characters so people understand what happened.',
    })
    expect(
      validateDeedForm(
        { ...INITIAL_DEED_FORM, category: 'animals', description: 'Fed a stray dog' },
        { description_min: 10 },
      ),
    ).toEqual({})
  })

  it('omits occurred_at for Just now and sends 0 people as null', () => {
    expect(
      toQrDeedRequest(scan, {
        ...INITIAL_DEED_FORM,
        category: 'animals',
        description: '  Fed a stray dog  ',
        people: 0,
      }),
    ).toEqual({
      method: 'qr_scan',
      scan_id: 's1',
      category: 'animals',
      description: 'Fed a stray dog',
      beneficiaries_count: null,
    })
  })

  it('sends scanned_at minus the offset for Earlier', () => {
    const body = toQrDeedRequest(scan, {
      ...INITIAL_DEED_FORM,
      category: 'animals',
      description: 'Fed a stray dog',
      when: 'earlier',
      earlierMinutes: 60,
    })
    expect(body.occurred_at).toBe('2026-11-17T09:00:00.000Z')
    expect(body.beneficiaries_count).toBe(1)
  })
})

describe('format helpers', () => {
  const now = new Date('2026-11-17T12:00:00+05:30')

  it('formats when a deed happened in IST', () => {
    expect(formatOccurredAt('2026-11-17T16:12:00+05:30', now)).toBe('Today, 4:12 pm')
    expect(formatOccurredAt('2026-11-16T09:05:00+05:30', now)).toBe('Yesterday, 9:05 am')
    expect(formatOccurredAt('2026-11-14T18:30:00+05:30', now)).toBe('14 November, 6:30 pm')
    expect(formatOccurredAt('nope', now)).toBe('')
  })

  it('formats minutes as hours when whole', () => {
    expect(formatMinutes(120)).toBe('2 hours')
    expect(formatMinutes(60)).toBe('1 hour')
    expect(formatMinutes(90)).toBe('90 minutes')
    expect(formatMinutes(1)).toBe('1 minute')
  })

  it('takes initials from the first and last names', () => {
    expect(initials('Ahmed Khan')).toBe('AK')
    expect(initials('Priya')).toBe('P')
    expect(initials('anil kumar reddy')).toBe('AR')
  })
})
