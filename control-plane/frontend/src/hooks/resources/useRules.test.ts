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
  useRules,
  useCreateRule,
  useUpdateRule,
  useDeleteRule,
  useOverlapCheck,
} from './useRules'

describe('useRules hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useRules', () => {
    it('queries rules list correctly when serviceId is provided', () => {
      useQuery.mockReturnValue({})
      useRules('srv-1')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['rules', 'srv-1'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/rules')
    })

    it('disables query when serviceId is null', () => {
      useQuery.mockReturnValue({})
      useRules(null)
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['rules', null],
          enabled: false,
        })
      )
    })
  })

  describe('useCreateRule', () => {
    it('submits create rule payload and invalidates correct caches', async () => {
      useMutation.mockReturnValue({})
      useCreateRule('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { priority: 10, protocol: 'tcp' as const, enabled: true }
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['rules', 'srv-1'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })

  describe('useUpdateRule', () => {
    it('submits patch rule payload and invalidates correct caches', async () => {
      useMutation.mockReturnValue({})
      useUpdateRule('srv-1', 'rule-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { priority: 12 }
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/rules/rule-1', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['rules', 'srv-1'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })

  describe('useDeleteRule', () => {
    it('calls delete rule endpoint and invalidates correct caches', async () => {
      useMutation.mockReturnValue({})
      useDeleteRule('srv-1', 'rule-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/rules/rule-1', {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['rules', 'srv-1'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['services'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['apply-status', 'srv-1'] })
    })
  })

  describe('useOverlapCheck', () => {
    it('performs overlap dry run without invalidating caches', async () => {
      useMutation.mockReturnValue({})
      useOverlapCheck('srv-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { protocol: 'tcp' as const, dst_port_lo: 80, dst_port_hi: 80 }
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/services/srv-1/rules/overlap-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
    })
  })
})
