import { describe, expect, it, vi, beforeEach } from 'vitest'

const { useQuery, useMutation, useQueryClient } = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  useQueryClient: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQuery(...args),
  useMutation: (...args: unknown[]) => useMutation(...args),
  useQueryClient: (...args: unknown[]) => useQueryClient(...args),
}))

const { apiClient } = vi.hoisted(() => ({
  apiClient: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  apiClient,
}))

// Import the hook to be implemented
import { useNodeControl } from './useNodeControl'

describe('useNodeControl hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  it('provides a health query with refetchInterval', () => {
    useQuery.mockReturnValue({ data: null, isLoading: true })
    useMutation.mockReturnValue({})

    useNodeControl()
    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['node-health'],
        refetchInterval: 2000,
      })
    )

    const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
    queryFn()
    expect(apiClient).toHaveBeenCalledWith('/node/health')
  })

  it('provides bypass mutation that updates status and invalidates query', async () => {
    useQuery.mockReturnValue({})
    useMutation.mockReturnValue({})

    useNodeControl()
    expect(useMutation).toHaveBeenCalled()

    // Find the call for the bypass mutation
    const bypassCallIdx = vi.mocked(useMutation).mock.calls.findIndex(
      (call) => call[0]?.mutationFn && String(call[0].mutationFn).includes('/node/bypass')
    )
    const mutationOpts = vi.mocked(useMutation).mock.calls[bypassCallIdx !== -1 ? bypassCallIdx : 0][0]

    const payload = { enabled: true, reason: 'Testing bypass' }
    await mutationOpts.mutationFn(payload)
    expect(apiClient).toHaveBeenCalledWith('/node/bypass', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    mutationOpts.onSuccess()
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['node-health'] })
  })

  it('provides maintenance mutation that updates status and invalidates query', async () => {
    useQuery.mockReturnValue({})
    useMutation.mockReturnValue({})

    useNodeControl()

    // Find the call for the maintenance mutation
    const maintCallIdx = vi.mocked(useMutation).mock.calls.findIndex(
      (call) => call[0]?.mutationFn && String(call[0].mutationFn).includes('/node/maintenance')
    )
    const mutationOpts = vi.mocked(useMutation).mock.calls[maintCallIdx !== -1 ? maintCallIdx : 1][0]

    const payload = { enabled: true }
    await mutationOpts.mutationFn(payload)
    expect(apiClient).toHaveBeenCalledWith('/node/maintenance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    mutationOpts.onSuccess()
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['node-health'] })
  })
})
