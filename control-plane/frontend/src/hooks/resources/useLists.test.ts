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
  useWhitelist,
  useAddWhitelist,
  useRemoveWhitelist,
} from './useLists'

describe('useLists hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useWhitelist', () => {
    it('queries whitelist correctly when serviceId is provided', () => {
      useQuery.mockReturnValue({})
      useWhitelist('srv-1')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['whitelist', 'srv-1'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/whitelist')
    })

    it('disables query when serviceId is null', () => {
      useQuery.mockReturnValue({})
      useWhitelist(null)
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['whitelist', null],
          enabled: false,
        })
      )
    })
  })

  describe('useAddWhitelist', () => {
    it('submits post request and invalidates correct caches', async () => {
      useMutation.mockReturnValue({})
      useAddWhitelist('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { source_cidr: '192.168.1.0/24' }
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/whitelist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['whitelist', 'srv-1'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })

  describe('useRemoveWhitelist', () => {
    it('submits delete request with query param and invalidates correct caches', async () => {
      useMutation.mockReturnValue({})
      useRemoveWhitelist('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const cidr = '192.168.1.0/24'
      await mutationOpts.mutationFn(cidr)
      expect(apiClient).toHaveBeenCalledWith(`/services/srv-1/whitelist?source_cidr=${encodeURIComponent(cidr)}`, {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['whitelist', 'srv-1'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })
})
