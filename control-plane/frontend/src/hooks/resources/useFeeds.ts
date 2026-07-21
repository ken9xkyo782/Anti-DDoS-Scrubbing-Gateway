import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type {
  FeedSourceResponse,
  FeedSyncRunResponse,
  FeedSyncAccepted,
  FeedFormat,
} from '../../api/types'

export function useFeeds() {
  return useQuery<FeedSourceResponse[]>({
    queryKey: ['feeds'],
    queryFn: () => apiClient<FeedSourceResponse[]>('/feeds'),
  })
}

export function useFeed(id: string | null) {
  return useQuery<FeedSourceResponse>({
    queryKey: ['feeds', id],
    queryFn: () => apiClient<FeedSourceResponse>(`/feeds/${id}`),
    enabled: id !== null,
  })
}

export function useCreateFeed() {
  const queryClient = useQueryClient()

  return useMutation<
    FeedSourceResponse,
    Error,
    {
      name: string
      url: string
      sync_interval_seconds: number
      format?: FeedFormat
      enabled?: boolean
      credential_env_var?: string | null
    }
  >({
    mutationFn: (payload) =>
      apiClient<FeedSourceResponse>('/feeds', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })
}

export function useUpdateFeed(id: string) {
  const queryClient = useQueryClient()

  return useMutation<
    FeedSourceResponse,
    Error,
    {
      name?: string
      url?: string
      sync_interval_seconds?: number
      format?: FeedFormat
      enabled?: boolean
      credential_env_var?: string | null
    }
  >({
    mutationFn: (payload) =>
      apiClient<FeedSourceResponse>(`/feeds/${id}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] })
      queryClient.invalidateQueries({ queryKey: ['feeds', id] })
    },
  })
}

export function useDeleteFeed(id: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, void>({
    mutationFn: () =>
      apiClient<void>(`/feeds/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })
}

export function useSyncFeed(id: string) {
  const queryClient = useQueryClient()

  return useMutation<FeedSyncAccepted, Error, { dry_run?: boolean } | void>({
    mutationFn: (params) => {
      const dryRun = params && typeof params === 'object' && params.dry_run ? '?dry_run=true' : ''
      return apiClient<FeedSyncAccepted>(`/feeds/${id}/sync${dryRun}`, {
        method: 'POST',
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] })
      queryClient.invalidateQueries({ queryKey: ['feeds', id] })
      queryClient.invalidateQueries({ queryKey: ['feeds', id, 'syncs'] })
    },
  })
}

export function useFeedSyncs(id: string | null) {
  return useQuery<FeedSyncRunResponse[]>({
    queryKey: ['feeds', id, 'syncs'],
    queryFn: () => apiClient<FeedSyncRunResponse[]>(`/feeds/${id}/syncs`),
    enabled: id !== null,
  })
}
