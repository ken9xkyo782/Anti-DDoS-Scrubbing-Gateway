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
  useFeeds,
  useFeed,
  useCreateFeed,
  useUpdateFeed,
  useDeleteFeed,
  useSyncFeed,
  useFeedSyncs,
} from './useFeeds'

describe('useFeeds hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useFeeds', () => {
    it('queries feeds list correctly', () => {
      useQuery.mockReturnValue({})
      useFeeds()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['feeds'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/feeds')
    })
  })

  describe('useFeed', () => {
    it('queries feed details correctly when id is provided', () => {
      useQuery.mockReturnValue({})
      useFeed('feed-1')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['feeds', 'feed-1'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/feeds/feed-1')
    })

    it('disables query when id is null', () => {
      useQuery.mockReturnValue({})
      useFeed(null)
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['feeds', null],
          enabled: false,
        })
      )
    })
  })

  describe('useCreateFeed', () => {
    it('submits create feed payload and invalidates feeds cache', async () => {
      useMutation.mockReturnValue({})
      useCreateFeed()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = {
        name: 'New Feed',
        url: 'https://example.com/feed',
        sync_interval_seconds: 600,
      }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/feeds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['feeds'] })
    })
  })

  describe('useUpdateFeed', () => {
    it('submits put payload and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useUpdateFeed('feed-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { name: 'Updated Feed' }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/feeds/feed-1', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['feeds'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['feeds', 'feed-1'] })
    })
  })

  describe('useDeleteFeed', () => {
    it('calls delete feed endpoint and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useDeleteFeed('feed-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/feeds/feed-1', {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['feeds'] })
    })
  })

  describe('useSyncFeed', () => {
    it('triggers sync with manual trigger', async () => {
      useMutation.mockReturnValue({})
      useSyncFeed('feed-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/feeds/feed-1/sync', {
        method: 'POST',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['feeds'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['feeds', 'feed-1'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['feeds', 'feed-1', 'syncs'] })
    })

    it('triggers sync with dry_run true', async () => {
      useMutation.mockReturnValue({})
      useSyncFeed('feed-1')
      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn({ dry_run: true })
      expect(apiClient).toHaveBeenCalledWith('/feeds/feed-1/sync?dry_run=true', {
        method: 'POST',
      })
    })
  })

  describe('useFeedSyncs', () => {
    it('queries feed syncs list correctly', () => {
      useQuery.mockReturnValue({})
      useFeedSyncs('feed-1')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['feeds', 'feed-1', 'syncs'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/feeds/feed-1/syncs')
    })
  })
})
