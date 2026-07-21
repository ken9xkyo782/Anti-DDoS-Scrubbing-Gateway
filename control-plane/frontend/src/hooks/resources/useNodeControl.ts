import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { NodeHealth } from '../useNodeTelemetry'

export interface NodeControlStateResponse {
  desired: boolean
  effective: boolean
  activated_at: string | null
  active_seconds: number
}

export function useNodeControl() {
  const queryClient = useQueryClient()

  const healthQuery = useQuery({
    queryKey: ['node-health'],
    queryFn: () => apiClient<NodeHealth>('/node/health'),
    refetchInterval: 2_000,
  })

  const bypassMutation = useMutation<
    NodeControlStateResponse,
    Error,
    { enabled: boolean; reason?: string }
  >({
    mutationFn: (payload) =>
      apiClient<NodeControlStateResponse>('/node/bypass', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['node-health'] })
    },
  })

  const maintenanceMutation = useMutation<
    NodeControlStateResponse,
    Error,
    { enabled: boolean }
  >({
    mutationFn: (payload) =>
      apiClient<NodeControlStateResponse>('/node/maintenance', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['node-health'] })
    },
  })

  return {
    healthQuery,
    bypassMutation,
    maintenanceMutation,
  }
}
