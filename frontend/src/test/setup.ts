import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { resetApiForTests } from '../lib/api'
import { resetDeviceIdForTests } from '../lib/deviceId'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
  resetApiForTests()
  resetDeviceIdForTests()
  try {
    window.localStorage.clear()
  } catch {
    // ignore
  }
})
