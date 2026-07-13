import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ServiceTelemetryPanel } from './ServiceTelemetryPanel'

const { useServiceTelemetry } = vi.hoisted(() => ({ useServiceTelemetry: vi.fn() }))

vi.mock('../hooks/useServiceTelemetry', () => ({
  useServiceTelemetry: (...args: unknown[]) => useServiceTelemetry(...args),
}))

describe('ServiceTelemetryPanel', () => {
  it('renders telemetry charts and rate tiles from the current service window', () => {
    useServiceTelemetry.mockReturnValue({
      data: {
        has_data: true,
        clean_pkts: 120,
        clean_bytes: 12_000,
        drop_pkts: 4,
        drop_bytes: 400,
        drop_by_reason: { rate_limit: 3, malformed: 1 },
        pps: 62,
        bps: 49_600,
        window_start: '2026-07-13T12:00:00Z',
        window_seconds: 2,
        stale: false,
      },
      isPending: false,
      isError: false,
    })

    render(<ServiceTelemetryPanel serviceId="service-1" />)

    expect(screen.getByRole('heading', { name: 'Service telemetry' })).toBeInTheDocument()
    expect(screen.getByText('62 pps')).toBeInTheDocument()
    expect(screen.getByText('49,600 bps')).toBeInTheDocument()
    expect(screen.getByLabelText('Clean versus dropped packets')).toBeInTheDocument()
    expect(screen.getByLabelText('Drops by reason')).toBeInTheDocument()
    expect(screen.getByText('Live telemetry')).toBeInTheDocument()
  })

  it('marks stale and empty telemetry windows clearly', () => {
    useServiceTelemetry.mockReturnValue({
      data: {
        has_data: false,
        clean_pkts: 0,
        clean_bytes: 0,
        drop_pkts: 0,
        drop_bytes: 0,
        drop_by_reason: {},
        pps: 0,
        bps: 0,
        window_start: null,
        window_seconds: 0,
        stale: true,
      },
      isPending: false,
      isError: false,
    })

    render(<ServiceTelemetryPanel serviceId="service-1" />)

    expect(screen.getByText('No telemetry data is available for this service.')).toBeInTheDocument()
    expect(screen.getByText('Stale telemetry')).toBeInTheDocument()
  })
})
