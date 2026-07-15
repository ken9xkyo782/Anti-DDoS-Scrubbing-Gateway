import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'

import './theme/tokens.css'
import './theme/base.css'
import { AdminDashboard } from './pages/AdminDashboard'

// Throwaway visual-preview entry: stubs the network so the real AdminDashboard
// renders with representative mock data (no backend/auth). Not shipped.
const now = Date.now()
const iso = (offsetSec: number) => new Date(now - offsetSec * 1000).toISOString()

const health = {
  has_data: true,
  xdp_mode: 'native',
  active_slot: 1,
  map_version: 128,
  map_error_count: 0,
  node_clean_bps: 34_000_000_000,
  node_capacity_bps: 40_000_000_000,
  window_start: iso(2),
  window_seconds: 2,
  stale: false,
  bloom_stats: { whitelist: 128, global_blacklist: 5_400, service_blacklist: 12 },
  committed_services: [
    { service_id: 'svc-alpha', observed_clean_bps: 2_100_000_000, committed_clean_bps: 2_000_000_000, honored: true, window_start: iso(2) },
    { service_id: 'svc-bravo', observed_clean_bps: 620_000_000, committed_clean_bps: 1_000_000_000, honored: false, window_start: iso(2) },
    { service_id: 'svc-charlie', observed_clean_bps: 480_000_000, committed_clean_bps: 500_000_000, honored: null, window_start: iso(2) },
  ],
  job_backlog: { queued: 3, applying: 1 },
  feed_sources: [
    { id: 'f1', name: 'Spamhaus DROP', enabled: true, last_status: 'success', last_error: null, last_sync_at: iso(30) },
    { id: 'f2', name: 'Emerging Threats', enabled: true, last_status: 'failed', last_error: 'upstream timeout (504)', last_sync_at: iso(600) },
    { id: 'f3', name: 'Internal blocklist', enabled: false, last_status: null, last_error: null, last_sync_at: null },
  ],
  bypass: { desired: false, effective: false, activated_at: null, active_seconds: 0 },
  maintenance: { desired: false, effective: false, activated_at: null, active_seconds: 0 },
  bypass_pkts: 0,
  bypass_bytes: 0,
}

const telemetry = {
  has_data: true,
  clean_pkts: 8_420_000,
  clean_bytes: 10_400_000_000,
  drop_pkts: 512_000,
  drop_bytes: 620_000_000,
  drop_by_reason: { rate_limit: 220_000, blacklist: 140_000, malformed: 90_000, syn_flood: 62_000 },
  pps: 1_240_000,
  bps: 34_000_000_000,
  top_dst_ports: [
    { port: 443, count: 1_820 },
    { port: 80, count: 940 },
    { port: 53, count: 410 },
  ],
  top_src: [
    { ip: '203.0.113.7', count: 1_200 },
    { ip: '198.51.100.44', count: 860 },
    { ip: '192.0.2.19', count: 530 },
  ],
  window_start: iso(2),
  window_seconds: 2,
  stale: false,
}

const history = {
  windows: Array.from({ length: 24 }, (_, i) => {
    const t = 24 - i
    return {
      window_start: iso(t * 2),
      clean_pkts: 7_600_000 + Math.round(Math.sin(i / 2) * 900_000) + i * 20_000,
      drop_pkts: 420_000 + Math.round(Math.cos(i / 3) * 120_000),
    }
  }),
}

const routes: Record<string, unknown> = {
  '/node/telemetry': telemetry,
  '/node/health': health,
  '/node/telemetry/history': history,
}

window.fetch = (async (input: RequestInfo | URL) => {
  const url = typeof input === 'string' ? input : input.toString()
  const key = Object.keys(routes).find((r) => url.includes(r) && !url.includes('/history')) ?? (url.includes('/history') ? '/node/telemetry/history' : undefined)
  const body = key ? routes[key] : {}
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof window.fetch

const params = new URLSearchParams(window.location.search)
if (params.get('theme') === 'dark') {
  document.documentElement.setAttribute('data-theme', 'dark')
} else {
  document.documentElement.setAttribute('data-theme', 'light')
}

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={queryClient}>
    <main style={{ padding: 'var(--space-6)', minHeight: '100vh', background: 'var(--bg)' }}>
      <AdminDashboard />
    </main>
  </QueryClientProvider>,
)
