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

import {
  useAmplificationConfig,
  useAddBlockedPort,
  useRemoveBlockedPort,
} from './useAmplificationConfig'

describe('useAmplificationConfig hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useAmplificationConfig', () => {
    it('queries amplification config correctly', () => {
      useQuery.mockReturnValue({})
      useAmplificationConfig()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['amplification-config'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/ddos/amplification')
    })
  })

  describe('useAddBlockedPort', () => {
    it('sends post request and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useAddBlockedPort()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { port: 123, note: 'NTP' }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/ddos/amplification/ports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['amplification-config'] })
    })
  })

  describe('useRemoveBlockedPort', () => {
    it('sends delete request to port URL and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useRemoveBlockedPort()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const port = 123

      await mutationOpts.mutationFn(port)
      expect(apiClient).toHaveBeenCalledWith(`/ddos/amplification/ports/${port}`, {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['amplification-config'] })
    })
  })
})
