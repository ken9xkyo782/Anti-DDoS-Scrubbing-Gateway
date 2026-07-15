import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type {
  AllocationResponse,
  AllocationUsageResponse,
  OverlapCheckResponse,
} from '../../api/types'

export function useAllocations(tenantId: string | null) {
  return useQuery<AllocationUsageResponse[]>({
    queryKey: ['allocations', tenantId],
    queryFn: () => apiClient<AllocationUsageResponse[]>(`/allocations?tenant_id=${tenantId}`),
    enabled: tenantId !== null,
  })
}

export function useMyAllocations() {
  return useQuery<AllocationResponse[]>({
    queryKey: ['my-allocations'],
    queryFn: () => apiClient<AllocationResponse[]>('/me/allocations'),
  })
}

export function useCreateAllocation() {
  const queryClient = useQueryClient()

  return useMutation<
    AllocationResponse,
    Error,
    { tenant_id: string; cidr: string }
  >({
    mutationFn: (payload) =>
      apiClient<AllocationResponse>('/allocations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['allocations'] })
      queryClient.invalidateQueries({ queryKey: ['allocations', variables.tenant_id] })
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
    },
  })
}

export function useRevokeAllocation(id: string, tenantId: string | null) {
  const queryClient = useQueryClient()

  return useMutation<AllocationResponse, Error, void>({
    mutationFn: () =>
      apiClient<AllocationResponse>(`/allocations/${id}/revoke`, {
        method: 'POST',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['allocations'] })
      if (tenantId) {
        queryClient.invalidateQueries({ queryKey: ['allocations', tenantId] })
      }
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
    },
  })
}

export function useCheckOverlap() {
  return useMutation<
    OverlapCheckResponse,
    Error,
    { cidr: string }
  >({
    mutationFn: (payload) =>
      apiClient<OverlapCheckResponse>('/allocations/overlap-check', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
  })
}
