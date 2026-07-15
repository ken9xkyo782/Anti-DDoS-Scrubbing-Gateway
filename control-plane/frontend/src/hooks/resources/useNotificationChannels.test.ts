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
  useNotificationChannels,
  useCreateNotificationChannel,
  useUpdateNotificationChannel,
  useDeleteNotificationChannel,
  useTestNotificationChannel,
} from './useNotificationChannels'

describe('useNotificationChannels hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useNotificationChannels', () => {
    it('queries notification channels correctly', () => {
      useQuery.mockReturnValue({})
      useNotificationChannels()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['notification-channels'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/alerts/channels')
    })
  })

  describe('useCreateNotificationChannel', () => {
    it('sends post request and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useCreateNotificationChannel()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { name: 'Email Team', kind: 'email' as const, config: { smtp_host: 'localhost', from: 'a@b.com', to: ['c@d.com'] } }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/alerts/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['notification-channels'] })
    })
  })

  describe('useUpdateNotificationChannel', () => {
    it('sends patch request and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useUpdateNotificationChannel('channel-123')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { name: 'Updated Email Team' }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/alerts/channels/channel-123', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['notification-channels'] })
    })
  })

  describe('useDeleteNotificationChannel', () => {
    it('sends delete request and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useDeleteNotificationChannel('channel-123')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]

      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/alerts/channels/channel-123', {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['notification-channels'] })
    })
  })

  describe('useTestNotificationChannel', () => {
    it('sends test request and returns results without invalidating cache', async () => {
      useMutation.mockReturnValue({})
      useTestNotificationChannel('channel-123')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]

      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/alerts/channels/channel-123/test', {
        method: 'POST',
      })

      // onSuccess should not invalidate cache as testing is transient
      if (mutationOpts.onSuccess) {
        mutationOpts.onSuccess()
      }
      expect(mockInvalidateQueries).not.toHaveBeenCalled()
    })
  })
})
