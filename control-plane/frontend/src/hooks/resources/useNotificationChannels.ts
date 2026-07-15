import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type {
  NotificationChannelResponse,
  NotificationChannelRequest,
  AlertChannelTestResponse,
} from '../../api/types'

export function useNotificationChannels() {
  return useQuery<NotificationChannelResponse[]>({
    queryKey: ['notification-channels'],
    queryFn: () => apiClient<NotificationChannelResponse[]>('/alerts/channels'),
  })
}

export function useCreateNotificationChannel() {
  const queryClient = useQueryClient()

  return useMutation<NotificationChannelResponse, Error, NotificationChannelRequest>({
    mutationFn: (payload) =>
      apiClient<NotificationChannelResponse>('/alerts/channels', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
    },
  })
}

export function useUpdateNotificationChannel(id: string) {
  const queryClient = useQueryClient()

  return useMutation<NotificationChannelResponse, Error, NotificationChannelRequest>({
    mutationFn: (payload) =>
      apiClient<NotificationChannelResponse>(`/alerts/channels/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
    },
  })
}

export function useDeleteNotificationChannel(id: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, void>({
    mutationFn: () =>
      apiClient<void>(`/alerts/channels/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
    },
  })
}

export function useTestNotificationChannel(id: string) {
  return useMutation<AlertChannelTestResponse, Error, void>({
    mutationFn: () =>
      apiClient<AlertChannelTestResponse>(`/alerts/channels/${encodeURIComponent(id)}/test`, {
        method: 'POST',
      }),
  })
}
