import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { BloomFpPanel } from './BloomFpPanel'

describe('BloomFpPanel', () => {
  afterEach(cleanup)

  it('renders labeled bloom false-positive counters', () => {
    render(
      <BloomFpPanel bloomStats={{ whitelist: 3, global_blacklist: 5_000, service_blacklist: 0 }} />,
    )

    expect(screen.getByText('Whitelist bloom')).toBeInTheDocument()
    expect(screen.getByText('5,000')).toBeInTheDocument()
  })

  it('colors a high false-positive count as a warning', () => {
    render(<BloomFpPanel bloomStats={{ global_blacklist: 5_000 }} />)

    expect(screen.getByText('5,000')).toHaveAttribute('data-severity', 'warning')
  })

  it('shows an empty state without bloom statistics', () => {
    render(<BloomFpPanel bloomStats={{}} />)

    expect(screen.getByText('No bloom filter statistics are available.')).toBeInTheDocument()
  })
})
