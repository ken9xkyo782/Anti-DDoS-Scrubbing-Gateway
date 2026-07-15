import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { RuleResponse, RuleOverlapCheckResponse, ApplyMutationResponse, Protocol } from '../../api/types'

export function useRules(serviceId: string | null) {
  return useQuery<RuleResponse[]>({
    queryKey: ['rules', serviceId],
    queryFn: () => apiClient<RuleResponse[]>(`/services/${serviceId}/rules`),
    enabled: serviceId !== null,
  })
}

export function useCreateRule(serviceId: string) {
  const queryClient = useQueryClient()

  return useMutation<
    ApplyMutationResponse,
    Error,
    {
      priority: number
      protocol: Protocol
      src_port_lo?: number | null
      src_port_hi?: number | null
      dst_port_lo?: number | null
      dst_port_hi?: number | null
      pps?: number | null
      bps?: number | null
      enabled: boolean
    }
  >({
    mutationFn: (payload) =>
      apiClient<ApplyMutationResponse>(`/services/${serviceId}/rules`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules', serviceId] })
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', serviceId] })
    },
  })
}

export function useUpdateRule(serviceId: string, ruleId: string) {
  const queryClient = useQueryClient()

  return useMutation<
    ApplyMutationResponse,
    Error,
    {
      priority?: number
      protocol?: Protocol
      src_port_lo?: number | null
      src_port_hi?: number | null
      dst_port_lo?: number | null
      dst_port_hi?: number | null
      pps?: number | null
      bps?: number | null
      enabled?: boolean
    }
  >({
    mutationFn: (payload) =>
      apiClient<ApplyMutationResponse>(`/services/${serviceId}/rules/${ruleId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules', serviceId] })
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', serviceId] })
    },
  })
}

export function useDeleteRule(serviceId: string, ruleId: string) {
  const queryClient = useQueryClient()

  return useMutation<ApplyMutationResponse, Error, void>({
    mutationFn: () =>
      apiClient<ApplyMutationResponse>(`/services/${serviceId}/rules/${ruleId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules', serviceId] })
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', serviceId] })
    },
  })
}

export function useOverlapCheck(serviceId: string) {
  return useMutation<
    RuleOverlapCheckResponse,
    Error,
    {
      protocol: Protocol
      src_port_lo?: number | null
      src_port_hi?: number | null
      dst_port_lo?: number | null
      dst_port_hi?: number | null
    }
  >({
    mutationFn: (payload) =>
      apiClient<RuleOverlapCheckResponse>(`/services/${serviceId}/rules/overlap-check`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
  })
}
