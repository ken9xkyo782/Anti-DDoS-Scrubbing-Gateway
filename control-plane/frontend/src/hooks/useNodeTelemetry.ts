import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../api/client'

export interface NodeTelemetry {
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

export interface NodeHealth {
  has_data: boolean
  xdp_mode: 'native' | 'generic' | 'offline' | 'unknown'
  active_slot: number | null
  map_version: number | null
  map_error_count: number
  node_clean_bps: number
  node_capacity_bps: number
  window_start: string | null
  window_seconds: number
  stale: boolean
  job_backlog: {
    queued: number
    applying: number
  }
  feed_sources: Array<{
    id: string
    name: string
    enabled: boolean
    last_status: string | null
    last_sync_at: string | null
  }>
}

export function useNodeTelemetry() {
  return useQuery({
    queryKey: ['node-telemetry'],
    queryFn: () => apiClient<NodeTelemetry>('/node/telemetry'),
    refetchInterval: 2_000,
  })
}

export function useNodeHealth() {
  return useQuery({
    queryKey: ['node-health'],
    queryFn: () => apiClient<NodeHealth>('/node/health'),
    refetchInterval: 2_000,
  })
}
