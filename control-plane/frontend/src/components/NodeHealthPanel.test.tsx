import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { NodeHealthPanel } from './NodeHealthPanel'

describe('NodeHealthPanel', () => {
  it('renders node health, live backlog, feed status, and throughput', () => {
    render(
      <NodeHealthPanel
        health={{
          has_data: true,
          xdp_mode: 'native',
          active_slot: 1,
          map_version: 42,
          map_error_count: 2,
          node_clean_bps: 20_000_000_000,
          node_capacity_bps: 40_000_000_000,
          window_start: '2026-07-13T12:00:00Z',
          window_seconds: 2,
          stale: false,
          job_backlog: { queued: 3, applying: 1 },
          feed_sources: [{ id: 'feed-1', name: 'Threat feed', enabled: true, last_status: 'success', last_sync_at: '2026-07-13T12:00:00Z' }],
          bypass: { desired: false, effective: false, activated_at: null, active_seconds: 0 },
          maintenance: { desired: false, effective: false, activated_at: null, active_seconds: 0 },
          bypass_pkts: 0,
          bypass_bytes: 0,
        }}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Node health' })).toBeInTheDocument()
    expect(screen.getByText('XDP mode: native')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('Queued jobs: 3')).toBeInTheDocument()
    expect(screen.getByText('Threat feed: success')).toBeInTheDocument()
    expect(screen.getByText('50.0% of capacity')).toBeInTheDocument()
  })
})
