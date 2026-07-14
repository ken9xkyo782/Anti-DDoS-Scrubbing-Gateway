import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { TrendChart } from './TrendChart'

describe('TrendChart', () => {
  afterEach(cleanup)

  it('renders a trend section from retained windows', () => {
    render(
      <TrendChart
        windows={[
          { window_start: '2026-07-14T00:00:00Z', clean_pkts: 100, drop_pkts: 5 },
          { window_start: '2026-07-14T00:00:02Z', clean_pkts: 120, drop_pkts: 6 },
        ]}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Trend' })).toBeInTheDocument()
    expect(screen.queryByText('No telemetry history is available yet.')).not.toBeInTheDocument()
  })

  it('shows an empty state without history', () => {
    render(<TrendChart windows={[]} />)

    expect(screen.getByText('No telemetry history is available yet.')).toBeInTheDocument()
  })
})
