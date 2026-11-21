import { describe, expect, it } from 'vitest'
import { firstName, formatResetDate } from './format'
import { pairLimitNotice, showPairWarning, totalLimitNotice, totalLimitState } from './limits'

const RESETS = '2026-12-01T00:00:00+05:30'
const DOER = { handle: 'ahmed', display_name: 'Ahmed Khan' }
// The seeded `deeds` config values (migration 0018): warn from 10 left in total, 3 for a pair.
const TOTAL_WARN_AT = 10
const PAIR_WARN_AT = 3

describe('thresholds (from config, D20)', () => {
  it.each([
    [150, 'ok'],
    [11, 'ok'],
    [10, 'near'],
    [1, 'near'],
    [0, 'full'],
    [-1, 'full'],
  ] as const)('total with %i left is %s', (remaining, state) => {
    expect(totalLimitState(remaining, TOTAL_WARN_AT)).toBe(state)
  })

  it.each([
    [15, false],
    [4, false],
    [3, true],
    [1, true],
    [0, false],
  ])('pair warning with %i left: %s', (remaining, shown) => {
    expect(showPairWarning(remaining, PAIR_WARN_AT)).toBe(shown)
  })

  it('follows other thresholds from config', () => {
    expect(totalLimitState(20, 25)).toBe('near')
    expect(totalLimitState(10, 5)).toBe('ok')
    expect(totalLimitState(0, 0)).toBe('full')
    expect(showPairWarning(5, 5)).toBe(true)
    expect(showPairWarning(3, 2)).toBe(false)
    expect(showPairWarning(1, 0)).toBe(false)
  })
})

describe('totalLimitNotice', () => {
  const options = (remaining: number) => ({
    reports_remaining_this_month: remaining,
    reporter_monthly_limit: 150,
    reporter_monthly_warn_at: TOTAL_WARN_AT,
    month_resets_at: RESETS,
  })

  it('shows nothing with more than 10 left', () => {
    expect(totalLimitNotice(options(11))).toBeNull()
  })

  it('counts down from 10 with the reset date', () => {
    expect(totalLimitNotice(options(10))).toEqual({
      tone: 'info',
      blocking: false,
      lead: '10 deeds left this month.',
      rest: 'Resets on 1 December.',
    })
    expect(totalLimitNotice(options(1))?.lead).toBe('1 deed left this month.')
  })

  it('uses the warning threshold from config', () => {
    expect(totalLimitNotice({ ...options(20), reporter_monthly_warn_at: 25 })?.lead).toBe(
      '20 deeds left this month.',
    )
    expect(totalLimitNotice({ ...options(8), reporter_monthly_warn_at: 5 })).toBeNull()
  })

  it('blocks at 0 with the limit from config', () => {
    expect(totalLimitNotice(options(0))).toEqual({
      tone: 'warning',
      blocking: true,
      lead: "You've recorded 150 deeds this month, the maximum.",
      rest: 'You can record more from 1 December.',
    })
    expect(totalLimitNotice({ ...options(0), reporter_monthly_limit: 200 })?.lead).toBe(
      "You've recorded 200 deeds this month, the maximum.",
    )
  })

  it('falls back to "next month" without a usable date', () => {
    expect(totalLimitNotice({ ...options(0), month_resets_at: 'nope' })?.rest).toBe(
      'You can record more next month.',
    )
    expect(totalLimitNotice({ ...options(5), month_resets_at: '' })?.rest).toBe(
      'Resets at the start of next month.',
    )
  })
})

describe('pairLimitNotice', () => {
  const scan = (remaining: number) => ({
    doer: DOER,
    pair_reports_remaining: remaining,
    month_resets_at: RESETS,
  })

  it('names the doer by first name from 3 left', () => {
    expect(pairLimitNotice(scan(3), PAIR_WARN_AT)).toEqual({
      tone: 'warning',
      blocking: false,
      lead: 'You can record Ahmed 3 more times this month.',
      rest: 'Resets on 1 December.',
    })
    expect(pairLimitNotice(scan(1), PAIR_WARN_AT)?.lead).toBe(
      'You can record Ahmed 1 more time this month.',
    )
  })

  it('shows nothing above 3 or at 0', () => {
    expect(pairLimitNotice(scan(4), PAIR_WARN_AT)).toBeNull()
    expect(pairLimitNotice(scan(0), PAIR_WARN_AT)).toBeNull()
  })

  it('uses the warning threshold from config', () => {
    expect(pairLimitNotice(scan(5), 5)?.lead).toBe('You can record Ahmed 5 more times this month.')
    expect(pairLimitNotice(scan(3), 2)).toBeNull()
  })
})

describe('format', () => {
  it('formats the reset date in IST, whatever the instant is written as', () => {
    expect(formatResetDate(RESETS)).toBe('1 December')
    // The same instant in UTC is still 30 November there, but 1 December in IST.
    expect(formatResetDate('2026-11-30T18:30:00Z')).toBe('1 December')
    expect(formatResetDate('2027-01-01T00:00:00+05:30')).toBe('1 January')
  })

  it('returns null for a missing or bad date', () => {
    expect(formatResetDate(null)).toBeNull()
    expect(formatResetDate(undefined)).toBeNull()
    expect(formatResetDate('not a date')).toBeNull()
  })

  it('takes the first name from a display name', () => {
    expect(firstName('Ahmed Khan')).toBe('Ahmed')
    expect(firstName('  Priya   Sharma ')).toBe('Priya')
    expect(firstName('Ravi')).toBe('Ravi')
  })
})
