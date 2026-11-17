import { describe, expect, it } from 'vitest'
import { firstName, formatResetDate } from './format'
import {
  PAIR_WARN_AT,
  pairLimitNotice,
  showPairWarning,
  TOTAL_WARN_AT,
  totalLimitNotice,
  totalLimitState,
} from './limits'

const RESETS = '2026-12-01T00:00:00+05:30'
const DOER = { handle: 'ahmed', display_name: 'Ahmed Khan' }

describe('thresholds', () => {
  it('warns from 10 left in total and from 3 left for a pair (D20)', () => {
    expect(TOTAL_WARN_AT).toBe(10)
    expect(PAIR_WARN_AT).toBe(3)
  })

  it.each([
    [150, 'ok'],
    [11, 'ok'],
    [10, 'near'],
    [1, 'near'],
    [0, 'full'],
    [-1, 'full'],
  ] as const)('total with %i left is %s', (remaining, state) => {
    expect(totalLimitState(remaining)).toBe(state)
  })

  it.each([
    [15, false],
    [4, false],
    [3, true],
    [1, true],
    [0, false],
  ])('pair warning with %i left: %s', (remaining, shown) => {
    expect(showPairWarning(remaining)).toBe(shown)
  })
})

describe('totalLimitNotice', () => {
  const options = (remaining: number) => ({
    reports_remaining_this_month: remaining,
    reporter_monthly_limit: 150,
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
    expect(pairLimitNotice(scan(3))).toEqual({
      tone: 'warning',
      blocking: false,
      lead: 'You can record Ahmed 3 more times this month.',
      rest: 'Resets on 1 December.',
    })
    expect(pairLimitNotice(scan(1))?.lead).toBe('You can record Ahmed 1 more time this month.')
  })

  it('shows nothing above 3 or at 0', () => {
    expect(pairLimitNotice(scan(4))).toBeNull()
    expect(pairLimitNotice(scan(0))).toBeNull()
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
