import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { WhitelistEntryResponse, BlacklistEntryResponse, ApplyMutationResponse } from '../../api/types'

export function useWhitelist(serviceId: string | null) {
  return useQuery<WhitelistEntryResponse[]>({
    queryKey: ['whitelist', serviceId],
    queryFn: () => apiClient<WhitelistEntryResponse[]>(`/services/${serviceId}/whitelist`),
    enabled: serviceId !== null,
  })
}

export function useAddWhitelist(serviceId: string) {
  const queryClient = useQueryClient()

  return useMutation<
    ApplyMutationResponse,
    Error,
    { source_cidr: string }
  >({
    mutationFn: (payload) =>
      apiClient<ApplyMutationResponse>(`/services/${serviceId}/whitelist`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whitelist', serviceId] })
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', serviceId] })
    },
  })
}

export function useRemoveWhitelist(serviceId: string) {
  const queryClient = useQueryClient()

  return useMutation<ApplyMutationResponse, Error, string>({
    mutationFn: (sourceCidr: string) =>
      apiClient<ApplyMutationResponse>(
        `/services/${serviceId}/whitelist?source_cidr=${encodeURIComponent(sourceCidr)}`,
        {
          method: 'DELETE',
        }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whitelist', serviceId] })
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', serviceId] })
    },
  })
}

export function useBlacklist(serviceId: string | null) {
  return useQuery<BlacklistEntryResponse[]>({
    queryKey: ['blacklist', serviceId],
    queryFn: () => apiClient<BlacklistEntryResponse[]>(`/services/${serviceId}/blacklist`),
    enabled: serviceId !== null,
  })
}

export function useAddBlacklist(serviceId: string) {
  const queryClient = useQueryClient()

  return useMutation<
    ApplyMutationResponse,
    Error,
    { source_cidr: string }
  >({
    mutationFn: (payload) =>
      apiClient<ApplyMutationResponse>(`/services/${serviceId}/blacklist`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['blacklist', serviceId] })
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', serviceId] })
    },
  })
}

export function useRemoveBlacklist(serviceId: string) {
  const queryClient = useQueryClient()

  return useMutation<ApplyMutationResponse, Error, string>({
    mutationFn: (sourceCidr: string) =>
      apiClient<ApplyMutationResponse>(
        `/services/${serviceId}/blacklist?source_cidr=${encodeURIComponent(sourceCidr)}`,
        {
          method: 'DELETE',
        }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['blacklist', serviceId] })
      queryClient.invalidateQueries({ queryKey: ['services'] })
      queryClient.invalidateQueries({ queryKey: ['apply-status', serviceId] })
    },
  })
}
