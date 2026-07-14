import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { CommittedHonoredPanel } from './CommittedHonoredPanel'

describe('CommittedHonoredPanel', () => {
  afterEach(cleanup)

  it('shows observed versus committed throughput and flags a breach', () => {
    render(
      <CommittedHonoredPanel
        services={[
          {
            service_id: 'svc-honored',
            observed_clean_bps: 2_000_000_000,
            committed_clean_bps: 1_000_000_000,
            honored: true,
          },
          {
            service_id: 'svc-breached',
            observed_clean_bps: 500_000_000,
            committed_clean_bps: 1_000_000_000,
            honored: false,
          },
        ]}
      />,
    )

    expect(screen.getByText('svc-honored')).toBeInTheDocument()
    expect(screen.getByText('Honored')).toHaveAttribute('data-severity', 'ok')
    expect(screen.getByText('Breached')).toHaveAttribute('data-severity', 'warning')
  })

  it('renders an empty state without committed plans', () => {
    render(<CommittedHonoredPanel services={[]} />)

    expect(screen.getByText('No services have a committed plan.')).toBeInTheDocument()
  })
})
