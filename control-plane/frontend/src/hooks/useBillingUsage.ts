import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../api/client'

export interface BillingUsage {
  service_id: string | null
  service_name: string
  tenant_id: string | null
  period_start: string
  period_end: string
  billing_metric: string
  committed_clean_gbps: string
  p95_clean_gbps: string
  billed_gbps: string
  overage_gbps: string
  overage_policy: 'billed' | 'capped'
  sample_count: number
  status: 'open' | 'final'
  provisional: boolean
}

export interface BillingUsageList {
  usage: BillingUsage[]
  has_data: boolean
}

export function useBillingUsage() {
  return useQuery({
    queryKey: ['billing-usage'],
    queryFn: () => apiClient<BillingUsageList>('/billing/usage'),
    refetchInterval: 30_000,
  })
}
