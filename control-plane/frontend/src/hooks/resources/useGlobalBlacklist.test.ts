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
  useGlobalBlacklist,
  useAddGlobalBlacklist,
  useRemoveGlobalBlacklist,
} from './useGlobalBlacklist'

describe('useGlobalBlacklist hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useGlobalBlacklist', () => {
    it('queries global blacklist correctly', () => {
      useQuery.mockReturnValue({})
      useGlobalBlacklist()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['global-blacklist'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/blacklist')
    })
  })

  describe('useAddGlobalBlacklist', () => {
    it('sends post request and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useAddGlobalBlacklist()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { source_cidr: '1.2.3.4/32' }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/blacklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['global-blacklist'] })
    })
  })

  describe('useRemoveGlobalBlacklist', () => {
    it('sends delete request with query param and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useRemoveGlobalBlacklist()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const cidr = '1.2.3.4/32'

      await mutationOpts.mutationFn(cidr)
      expect(apiClient).toHaveBeenCalledWith(`/blacklist?source_cidr=${encodeURIComponent(cidr)}`, {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['global-blacklist'] })
    })
  })
})
