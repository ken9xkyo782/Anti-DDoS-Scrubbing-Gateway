import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AlertsPanel } from './AlertsPanel'

const { useAlerts } = vi.hoisted(() => ({ useAlerts: vi.fn() }))

vi.mock('../hooks/useAlerts', () => ({
  useAlerts: (...args: unknown[]) => useAlerts(...args),
}))

afterEach(cleanup)

describe('AlertsPanel', () => {
  it('shows loading and empty states', () => {
    useAlerts.mockReturnValue({ isPending: true, isError: false })
    const { rerender } = render(<AlertsPanel />)
    expect(screen.getByText('Loading alerts…')).toBeInTheDocument()

    useAlerts.mockReturnValue({ data: { alerts: [], has_data: false }, isPending: false, isError: false })
    rerender(<AlertsPanel />)
    expect(screen.getByText('No alerts are active or recorded yet.')).toBeInTheDocument()
  })

  it('shows active and resolved alerts with severity color and delivery state', () => {
    useAlerts.mockReturnValue({
      data: {
        has_data: true,
        alerts: [
          {
            id: 'alert-1', rule_key: 'map_error', scope: 'node', scope_key: 'node', service_id: null,
            tenant_id: null, service_name: null, severity: 'critical', state: 'firing', metric_value: '1',
            context: {}, first_observed_at: '2026-07-14T00:00:00Z', fired_at: '2026-07-14T00:00:00Z',
            resolved_at: null, acknowledged_at: null, notifications: [{ state: 'sent' }],
          },
          {
            id: 'alert-2', rule_key: 'attack_onset', scope: 'service', scope_key: 'service-1', service_id: 'service-1',
            tenant_id: 'tenant-1', service_name: 'Payments', severity: 'warning', state: 'resolved', metric_value: '0.2',
            context: {}, first_observed_at: '2026-07-14T00:00:00Z', fired_at: '2026-07-14T00:00:00Z',
            resolved_at: '2026-07-14T00:01:00Z', acknowledged_at: null, notifications: [{ state: 'failed' }],
          },
        ],
      },
      isPending: false,
      isError: false,
    })

    render(<AlertsPanel />)

    expect(screen.getByText('critical')).toHaveStyle({ color: 'var(--color-critical)' })
    expect(screen.getByText('firing')).toBeInTheDocument()
    expect(screen.getByText('resolved')).toBeInTheDocument()
    expect(screen.getByText('sent')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
  })
})
