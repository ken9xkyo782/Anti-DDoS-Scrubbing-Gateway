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

export interface TelemetryWindowPoint {
  window_start: string
  window_seconds: number
  clean_pkts: number
  clean_bytes: number
  drop_pkts: number
  drop_bytes: number
  pps: number
  bps: number
}

export interface TelemetryHistory {
  has_data: boolean
  windows: TelemetryWindowPoint[]
}

export function useServiceTelemetry(serviceId: string | null) {
  return useQuery({
    queryKey: ['service-telemetry', serviceId],
    queryFn: () => apiClient<ServiceTelemetry>(`/services/${serviceId}/telemetry`),
    enabled: serviceId !== null,
    refetchInterval: 2_000,
  })
}

export function useServiceTelemetryHistory(serviceId: string | null) {
  return useQuery({
    queryKey: ['service-telemetry-history', serviceId],
    queryFn: () => apiClient<TelemetryHistory>(`/services/${serviceId}/telemetry/history`),
    enabled: serviceId !== null,
    refetchInterval: 2_000,
  })
}
