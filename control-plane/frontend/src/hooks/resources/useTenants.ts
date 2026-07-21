import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { TenantResponse, TenantStatus } from '../../api/types'

export function useTenants() {
  return useQuery<TenantResponse[]>({
    queryKey: ['tenants'],
    queryFn: () => apiClient<TenantResponse[]>('/tenants'),
  })
}

export function useTenant(id: string | null) {
  return useQuery<TenantResponse>({
    queryKey: ['tenants', id],
    queryFn: () => apiClient<TenantResponse>(`/tenants/${id}`),
    enabled: id !== null,
  })
}

export function useCreateTenant() {
  const queryClient = useQueryClient()

  return useMutation<
    TenantResponse,
    Error,
    { name: string }
  >({
    mutationFn: (payload) =>
      apiClient<TenantResponse>('/tenants', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
    },
  })
}

export function useUpdateTenant(id: string) {
  const queryClient = useQueryClient()

  return useMutation<
    TenantResponse,
    Error,
    { name?: string; status?: TenantStatus }
  >({
    mutationFn: (payload) =>
      apiClient<TenantResponse>(`/tenants/${id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
      queryClient.invalidateQueries({ queryKey: ['tenants', id] })
    },
  })
}

export function useDeleteTenant(id: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, void>({
    mutationFn: () =>
      apiClient<void>(`/tenants/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}

export function useSuspendTenant(id: string) {
  const queryClient = useQueryClient()

  return useMutation<TenantResponse, Error, void>({
    mutationFn: () =>
      apiClient<TenantResponse>(`/tenants/${id}/suspend`, {
        method: 'POST',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
      queryClient.invalidateQueries({ queryKey: ['tenants', id] })
    },
  })
}

export function useReactivateTenant(id: string) {
  const queryClient = useQueryClient()

  return useMutation<TenantResponse, Error, void>({
    mutationFn: () =>
      apiClient<TenantResponse>(`/tenants/${id}/reactivate`, {
        method: 'POST',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
      queryClient.invalidateQueries({ queryKey: ['tenants', id] })
    },
  })
}
