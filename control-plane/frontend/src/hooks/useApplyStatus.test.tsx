import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

const { useQuery } = vi.hoisted(() => ({ useQuery: vi.fn() }))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQuery(...args),
}))

import { useApplyStatus } from './useApplyStatus'

interface QueryOptionsMock {
  refetchInterval: (query: { state: { data: { apply_status?: string } | undefined } }) => number | false | undefined
}

describe('useApplyStatus', () => {
  beforeEach(() => {
    useQuery.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('configures query and returns correct interval for non-terminal statuses', () => {
    useQuery.mockReturnValue({
      data: { apply_status: 'pending' },
      isPending: false,
    })

    renderHook(() => useApplyStatus('srv-1'))

    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['apply-status', 'srv-1'],
        enabled: true,
      })
    )

    const lastCall = vi.mocked(useQuery).mock.calls[0][0] as QueryOptionsMock
    const refetchInterval = lastCall.refetchInterval

    // Test that it returns 1000 for non-terminal statuses
    expect(refetchInterval({ state: { data: { apply_status: 'pending' } } })).toBe(1000)
    expect(refetchInterval({ state: { data: { apply_status: 'queued' } } })).toBe(1000)
    expect(refetchInterval({ state: { data: { apply_status: 'applying' } } })).toBe(1000)
  })

  it('stops polling (returns false) when status becomes terminal', () => {
    useQuery.mockReturnValue({
      data: { apply_status: 'active' },
      isPending: false,
    })

    renderHook(() => useApplyStatus('srv-1'))

    const lastCall = vi.mocked(useQuery).mock.calls[0][0] as QueryOptionsMock
    const refetchInterval = lastCall.refetchInterval

    // Test that it returns false for terminal statuses
    expect(refetchInterval({ state: { data: { apply_status: 'active' } } })).toBe(false)
    expect(refetchInterval({ state: { data: { apply_status: 'failed' } } })).toBe(false)
  })

  it('triggers takingLonger and slows down polling to 5s after 30s', () => {
    vi.useFakeTimers()

    // 1. Initial render - status is pending
    useQuery.mockReturnValue({
      data: { apply_status: 'pending' },
      isPending: false,
    })

    const { result, rerender } = renderHook(() => useApplyStatus('srv-1'))

    expect(result.current.takingLonger).toBe(false)

    // 2. Advance time by 30 seconds
    act(() => {
      vi.advanceTimersByTime(30000)
    })

    // Re-render hook to pick up new state
    rerender()

    expect(result.current.takingLonger).toBe(true)

    // 3. Get the refetchInterval option from the latest call
    const lastCallIdx = vi.mocked(useQuery).mock.calls.length - 1
    const lastCall = vi.mocked(useQuery).mock.calls[lastCallIdx][0] as QueryOptionsMock
    const refetchInterval = lastCall.refetchInterval

    // Under takingLonger = true, refetchInterval should return 5000 for pending
    expect(refetchInterval({ state: { data: { apply_status: 'pending' } } })).toBe(5000)

    vi.useRealTimers()
  })

  it('does not poll when disabled or serviceId is null', () => {
    useQuery.mockReturnValue({
      data: undefined,
      isPending: true,
    })

    // Render with null serviceId
    renderHook(() => useApplyStatus(null))
    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['apply-status', null],
        enabled: false,
      })
    )

    const lastCall = vi.mocked(useQuery).mock.calls[0][0] as QueryOptionsMock
    expect(lastCall.refetchInterval({ state: { data: undefined } })).toBe(false)

    // Render with enabled: false option
    useQuery.mockClear()
    useQuery.mockReturnValue({
      data: undefined,
      isPending: true,
    })
    renderHook(() => useApplyStatus('srv-1', { enabled: false }))
    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['apply-status', 'srv-1'],
        enabled: false,
      })
    )
  })
})
