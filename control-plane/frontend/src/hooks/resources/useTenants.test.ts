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
  useTenants,
  useTenant,
  useCreateTenant,
  useUpdateTenant,
  useDeleteTenant,
  useSuspendTenant,
  useReactivateTenant,
} from './useTenants'

describe('useTenants hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useTenants', () => {
    it('queries tenants list correctly', () => {
      useQuery.mockReturnValue({})
      useTenants()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['tenants'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/tenants')
    })
  })

  describe('useTenant', () => {
    it('queries tenant details correctly when id is provided', () => {
      useQuery.mockReturnValue({})
      useTenant('tenant-1')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['tenants', 'tenant-1'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/tenants/tenant-1')
    })

    it('disables query when id is null', () => {
      useQuery.mockReturnValue({})
      useTenant(null)
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['tenants', null],
          enabled: false,
        })
      )
    })
  })

  describe('useCreateTenant', () => {
    it('submits create tenant payload and invalidates tenants cache', async () => {
      useMutation.mockReturnValue({})
      useCreateTenant()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { name: 'New Tenant' }
      
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/tenants', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
    })
  })

  describe('useUpdateTenant', () => {
    it('submits patch payload and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useUpdateTenant('tenant-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { name: 'Updated Tenant', status: 'suspended' as const }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/tenants/tenant-1', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants', 'tenant-1'] })
    })
  })

  describe('useDeleteTenant', () => {
    it('calls delete tenant endpoint and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useDeleteTenant('tenant-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/tenants/tenant-1', {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['users'] })
    })
  })

  describe('useSuspendTenant', () => {
    it('calls suspend tenant endpoint and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useSuspendTenant('tenant-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/tenants/tenant-1/suspend', {
        method: 'POST',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants', 'tenant-1'] })
    })
  })

  describe('useReactivateTenant', () => {
    it('calls reactivate tenant endpoint and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useReactivateTenant('tenant-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/tenants/tenant-1/reactivate', {
        method: 'POST',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants', 'tenant-1'] })
    })
  })
})
