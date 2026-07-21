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

import { useAlertRules, useUpdateAlertRule } from './useAlertRules'

describe('useAlertRules hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useAlertRules', () => {
    it('queries alert rules correctly', () => {
      useQuery.mockReturnValue({})
      useAlertRules()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['alert-rules'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/alerts/rules')
    })
  })

  describe('useUpdateAlertRule', () => {
    it('sends patch request and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useUpdateAlertRule('test-rule')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { enabled: false, severity: 'critical' as const }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/alerts/rules/test-rule', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['alert-rules'] })
    })
  })
})
