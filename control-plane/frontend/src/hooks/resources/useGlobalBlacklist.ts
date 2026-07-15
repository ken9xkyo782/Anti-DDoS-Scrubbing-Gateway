import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { BlacklistEntryResponse } from '../../api/types'

export function useGlobalBlacklist() {
  return useQuery<BlacklistEntryResponse[]>({
    queryKey: ['global-blacklist'],
    queryFn: () => apiClient<BlacklistEntryResponse[]>('/blacklist'),
  })
}

export function useAddGlobalBlacklist() {
  const queryClient = useQueryClient()

  return useMutation<
    BlacklistEntryResponse,
    Error,
    { source_cidr: string }
  >({
    mutationFn: (payload) =>
      apiClient<BlacklistEntryResponse>('/blacklist', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['global-blacklist'] })
    },
  })
}

export function useRemoveGlobalBlacklist() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, string>({
    mutationFn: (sourceCidr: string) =>
      apiClient<void>(`/blacklist?source_cidr=${encodeURIComponent(sourceCidr)}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['global-blacklist'] })
    },
  })
}
