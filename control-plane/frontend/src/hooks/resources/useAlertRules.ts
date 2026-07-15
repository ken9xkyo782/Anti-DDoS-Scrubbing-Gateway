import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { AlertRuleResponse, AlertRulePatchRequest } from '../../api/types'

export function useAlertRules() {
  return useQuery<AlertRuleResponse[]>({
    queryKey: ['alert-rules'],
    queryFn: () => apiClient<AlertRuleResponse[]>('/alerts/rules'),
  })
}

export function useUpdateAlertRule(key: string) {
  const queryClient = useQueryClient()

  return useMutation<AlertRuleResponse, Error, AlertRulePatchRequest>({
    mutationFn: (payload) =>
      apiClient<AlertRuleResponse>(`/alerts/rules/${encodeURIComponent(key)}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alert-rules'] })
    },
  })
}
