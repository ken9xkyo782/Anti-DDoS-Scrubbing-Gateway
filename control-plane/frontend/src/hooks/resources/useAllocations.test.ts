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
  useAllocations,
  useMyAllocations,
  useCreateAllocation,
  useRevokeAllocation,
  useCheckOverlap,
} from './useAllocations'

describe('useAllocations hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useAllocations', () => {
    it('queries tenant allocations correctly when tenantId is provided', () => {
      useQuery.mockReturnValue({})
      useAllocations('tenant-123')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['allocations', 'tenant-123'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/allocations?tenant_id=tenant-123')
    })

    it('disables query when tenantId is null', () => {
      useQuery.mockReturnValue({})
      useAllocations(null)
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['allocations', null],
          enabled: false,
        })
      )
    })
  })

  describe('useMyAllocations', () => {
    it('queries own allocations correctly', () => {
      useQuery.mockReturnValue({})
      useMyAllocations()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['my-allocations'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/me/allocations')
    })
  })

  describe('useCreateAllocation', () => {
    it('submits create allocation payload and invalidates related caches', async () => {
      useMutation.mockReturnValue({})
      useCreateAllocation()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { tenant_id: 'tenant-123', cidr: '10.0.0.0/24' }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/allocations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess(null, payload)
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['allocations'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['allocations', 'tenant-123'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
    })
  })

  describe('useRevokeAllocation', () => {
    it('submits revoke allocation request and invalidates related caches', async () => {
      useMutation.mockReturnValue({})
      useRevokeAllocation('alloc-456', 'tenant-123')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]

      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/allocations/alloc-456/revoke', {
        method: 'POST',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['allocations'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['allocations', 'tenant-123'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
    })
  })

  describe('useCheckOverlap', () => {
    it('submits check overlap request correctly', async () => {
      useMutation.mockReturnValue({})
      useCheckOverlap()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { cidr: '10.0.0.0/24' }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/allocations/overlap-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
    })
  })
})
