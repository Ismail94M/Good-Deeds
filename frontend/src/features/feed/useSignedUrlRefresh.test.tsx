import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { makePhoto } from './testFixtures'
import { MAX_TIMER_MS, REFRESH_MARGIN_MS, useSignedUrlRefresh } from './useSignedUrlRefresh'

describe('useSignedUrlRefresh', () => {
  it('caps a far-future delay instead of overflowing into an immediate refetch', () => {
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout')
    const refetch = vi.fn()
    renderHook(() => useSignedUrlRefresh([makePhoto(1)], refetch))
    expect(refetch).not.toHaveBeenCalled()
    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), MAX_TIMER_MS)
  })

  it('refetches five minutes before the earliest URL expires', () => {
    vi.useFakeTimers()
    const refetch = vi.fn()
    const expires = new Date(Date.now() + 30 * 60_000).toISOString()
    renderHook(() =>
      useSignedUrlRefresh([makePhoto(1), makePhoto(2, { urls_expire_at: expires })], refetch),
    )
    vi.advanceTimersByTime(30 * 60_000 - REFRESH_MARGIN_MS - 1000)
    expect(refetch).not.toHaveBeenCalled()
    vi.advanceTimersByTime(2000)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('throttles refetches on image errors', () => {
    const refetch = vi.fn()
    const { result } = renderHook(() => useSignedUrlRefresh([], refetch))
    result.current()
    result.current()
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})
