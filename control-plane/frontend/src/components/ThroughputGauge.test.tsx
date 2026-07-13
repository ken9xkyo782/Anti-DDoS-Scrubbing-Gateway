import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ThroughputGauge } from './ThroughputGauge'

describe('ThroughputGauge', () => {
  it('computes current clean throughput as a share of capacity', () => {
    render(<ThroughputGauge cleanBps={20_000_000_000} capacityBps={40_000_000_000} />)

    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '50')
    expect(screen.getByText('50.0% of capacity')).toBeInTheDocument()
  })
})
