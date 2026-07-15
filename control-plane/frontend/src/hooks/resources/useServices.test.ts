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
  useServices,
  useService,
  useCreateService,
  useUpdateService,
  useDeleteService,
  useEnableService,
  useDisableService,
} from './useServices'

describe('useServices hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useServices', () => {
    it('queries services list correctly', () => {
      useQuery.mockReturnValue({})
      useServices()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['services'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/services')
    })
  })

  describe('useService', () => {
    it('queries service details correctly when id is provided', () => {
      useQuery.mockReturnValue({})
      useService('srv-1')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['services', 'srv-1'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1')
    })

    it('disables query when id is null', () => {
      useQuery.mockReturnValue({})
      useService(null)
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['services', null],
          enabled: false,
        })
      )
    })
  })

  describe('useCreateService', () => {
    it('submits create service payload and invalidates services cache', async () => {
      useMutation.mockReturnValue({})
      useCreateService()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { name: 'New Srv', cidr_or_ip: '10.0.0.0/24', mode: 'allow-rule-only' }
      
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/services', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
    })
  })

  describe('useUpdateService', () => {
    it('submits patch payload and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useUpdateService('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { name: 'Updated Srv' }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })

  describe('useDeleteService', () => {
    it('calls delete service endpoint and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useDeleteService('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1', {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })

  describe('useEnableService', () => {
    it('calls enable service endpoint and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useEnableService('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/enable', {
        method: 'POST',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })

  describe('useDisableService', () => {
    it('submits disable payload and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useDisableService('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { confirm: true }
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })
})
