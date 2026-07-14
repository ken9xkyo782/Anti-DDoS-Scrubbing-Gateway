import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthContext, type AuthContextValue } from '../auth/AuthContext'
import type { NodeHealth } from '../hooks/useNodeTelemetry'
import { AppLayout } from '../layout/AppLayout'

const { useNodeHealth } = vi.hoisted(() => ({ useNodeHealth: vi.fn() }))

vi.mock('../hooks/useNodeTelemetry', () => ({
  useNodeHealth: () => useNodeHealth(),
}))

import { NodeControlBanner } from './NodeControlBanner'

function health({ bypass = false, maintenance = false } = {}): NodeHealth {
  return {
    has_data: true,
    xdp_mode: 'native',
    active_slot: 1,
    map_version: 42,
    map_error_count: 0,
    node_clean_bps: 0,
    node_capacity_bps: 0,
    window_start: '2026-07-14T12:00:00Z',
    window_seconds: 2,
    stale: false,
    job_backlog: { queued: 0, applying: 0 },
    feed_sources: [],
    bypass: { desired: bypass, effective: bypass, activated_at: null, active_seconds: 0 },
    maintenance: {
      desired: maintenance,
      effective: maintenance,
      activated_at: null,
      active_seconds: 0,
    },
    bypass_pkts: 0,
    bypass_bytes: 0,
  }
}

function renderAppShell() {
  const auth: AuthContextValue = {
    principal: {
      id: 'a4f3df34-a15b-4482-9e16-5b5604c7ae9d',
      username: 'admin',
      role: 'admin',
      tenant_id: null,
    },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }

  render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter initialEntries={['/billing']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/billing" element={<h1>Billing</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('NodeControlBanner', () => {
  afterEach(() => {
    cleanup()
    vi.resetAllMocks()
  })

  it('renders critical bypass and maintenance indicators from node health', () => {
    useNodeHealth.mockReturnValue({ data: health({ bypass: true, maintenance: true }) })

    render(<NodeControlBanner />)

    expect(screen.getByRole('alert')).toHaveTextContent('BYPASS ACTIVE')
    expect(screen.getByRole('status')).toHaveTextContent('MAINTENANCE')
  })

  it('clears both indicators when their effective states clear', () => {
    useNodeHealth.mockReturnValue({ data: health({ bypass: true, maintenance: true }) })
    const { rerender } = render(<NodeControlBanner />)

    useNodeHealth.mockReturnValue({ data: health() })
    rerender(<NodeControlBanner />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('renders the banner in the shared app shell for a routed view', () => {
    useNodeHealth.mockReturnValue({ data: health({ bypass: true }) })

    renderAppShell()

    expect(screen.getByRole('alert')).toHaveTextContent('BYPASS ACTIVE')
    expect(screen.getByRole('heading', { name: 'Billing' })).toBeInTheDocument()
  })
})
