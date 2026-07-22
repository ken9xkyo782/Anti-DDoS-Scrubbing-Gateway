import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { AmplificationConfigResponse, BlockedPortResponse } from '../../api/types'

export function useAmplificationConfig() {
  return useQuery<AmplificationConfigResponse>({
    queryKey: ['amplification-config'],
    queryFn: () => apiClient<AmplificationConfigResponse>('/ddos/amplification'),
  })
}

export function useAddBlockedPort() {
  const queryClient = useQueryClient()

  return useMutation<
    BlockedPortResponse,
    Error,
    { port: number; note?: string | null }
  >({
    mutationFn: (payload) =>
      apiClient<BlockedPortResponse>('/ddos/amplification/ports', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['amplification-config'] })
    },
  })
}

export function useRemoveBlockedPort() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, number>({
    mutationFn: (port: number) =>
      apiClient<void>(`/ddos/amplification/ports/${port}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['amplification-config'] })
    },
  })
}
