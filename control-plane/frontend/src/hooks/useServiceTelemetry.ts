import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../api/client'

export interface ServiceTelemetry {
  has_data: boolean
  clean_pkts: number
  clean_bytes: number
  drop_pkts: number
  drop_bytes: number
  drop_by_reason: Record<string, number>
  pps: number
  bps: number
  window_start: string | null
  window_seconds: number
  stale: boolean
}

export function useServiceTelemetry(serviceId: string | null) {
  return useQuery({
    queryKey: ['service-telemetry', serviceId],
    queryFn: () => apiClient<ServiceTelemetry>(`/services/${serviceId}/telemetry`),
    enabled: serviceId !== null,
    refetchInterval: 2_000,
  })
}
