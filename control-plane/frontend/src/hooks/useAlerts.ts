import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../api/client'

export type AlertSeverity = 'info' | 'warning' | 'critical'
export type AlertState = 'pending' | 'firing' | 'resolved'

export interface AlertNotification {
  state: 'pending' | 'sent' | 'retrying' | 'failed'
}

export interface AlertRecord {
  id: string
  rule_key: string
  scope: 'node' | 'service'
  scope_key: string
  service_id: string | null
  tenant_id: string | null
  service_name: string | null
  severity: AlertSeverity
  state: AlertState
  metric_value: string | number | null
  context: Record<string, unknown>
  first_observed_at: string
  fired_at: string | null
  resolved_at: string | null
  acknowledged_at: string | null
  notifications: AlertNotification[]
}

export interface AlertList {
  alerts: AlertRecord[]
  has_data: boolean
}

export function useAlerts() {
  return useQuery({
    queryKey: ['alerts'],
    queryFn: () => apiClient<AlertList>('/alerts'),
    refetchInterval: 2_000,
  })
}
