import { useQuery } from '@tanstack/react-query'

import { apiClient } from '../api/client'
import type { TopPort, TopSource } from './useServiceTelemetry'

export interface NodeTelemetry {
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
  window_start: string | null
  window_seconds: number
  stale: boolean
}

export interface CommittedService {
  service_id: string
  observed_clean_bps: number
  committed_clean_bps: number
  honored: boolean | null
  window_start: string | null
}

export interface FeedSyncRunStatus {
  id: string
  sequence: number
  status: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  error: string | null
  valid: number
  added: number
  removed: number
  skipped_invalid: number
  overlap_count: number
}

export interface FeedSource {
  id: string
  name: string
  enabled: boolean
  last_status: string | null
  last_error?: string | null
  last_sync_at: string | null
  last_run?: FeedSyncRunStatus | null
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
  bloom_stats?: Record<string, number>
  committed_services?: CommittedService[]
  job_backlog: {
    queued: number
    applying: number
  }
  feed_sources: FeedSource[]
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
