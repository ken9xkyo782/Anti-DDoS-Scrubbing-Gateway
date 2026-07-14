import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../api/client'

export interface TopPort {
  port: number
  count: number
}

export interface TopSource {
  ip: string
  count: number
}

export interface ServiceTelemetry {
  has_data: boolean
  clean_pkts: number
  clean_bytes: number
  drop_pkts: number
  drop_bytes: number
  drop_by_reason: Record<string, number>
  pps: number
  bps: number
  top_dst_ports: TopPort[]
  top_src: TopSource[]
  committed_clean_bps: number
  committed_honored: boolean | null
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
