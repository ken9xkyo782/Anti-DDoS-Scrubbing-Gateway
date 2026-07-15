import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { ServiceResponse, ApplyMutationResponse } from '../../api/types'

export function useServices() {
  return useQuery<ServiceResponse[]>({
    queryKey: ['services'],
    queryFn: () => apiClient<ServiceResponse[]>('/services'),
  })
}

export function useService(id: string | null) {
  return useQuery<ServiceResponse>({
    queryKey: ['services', id],
    queryFn: () => apiClient<ServiceResponse>(`/services/${id}`),
    enabled: id !== null,
  })
}

export function useCreateService() {
  const queryClient = useQueryClient()

  return useMutation<
    ApplyMutationResponse,
    Error,
    {
      name: string
      cidr_or_ip: string
      mode: string
      vip_pps?: number | null
      vip_bps?: number | null
      tenant_id?: string
      plan?: {
        committed_clean_gbps: number
        ceiling_clean_gbps: number
      } | null
    }
  >({
    mutationFn: (payload) =>
      apiClient<ApplyMutationResponse>('/services', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services'] })
    },
  })
}

export function useUpdateService(id: string) {
  const queryClient = useQueryClient()

  return useMutation<
    ApplyMutationResponse,
    Error,
    {
      name?: string
      cidr_or_ip?: string
      mode?: string
      vip_pps?: number | null
      vip_bps?: number | null
    }
  >({
    mutationFn: (payload) =>
      apiClient<ApplyMutationResponse>(`/services/${id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', id] })
    },
  })
}

export function useDeleteService(id: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, void>({
    mutationFn: () =>
      apiClient<void>(`/services/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', id] })
    },
  })
}

export function useEnableService(id: string) {
  const queryClient = useQueryClient()

  return useMutation<ApplyMutationResponse, Error, void>({
    mutationFn: () =>
      apiClient<ApplyMutationResponse>(`/services/${id}/enable`, {
        method: 'POST',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', id] })
    },
  })
}

export function useDisableService(id: string) {
  const queryClient = useQueryClient()

  return useMutation<ApplyMutationResponse, Error, { confirm: boolean }>({
    mutationFn: (payload) =>
      apiClient<ApplyMutationResponse>(`/services/${id}/disable`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', id] })
    },
  })
}
