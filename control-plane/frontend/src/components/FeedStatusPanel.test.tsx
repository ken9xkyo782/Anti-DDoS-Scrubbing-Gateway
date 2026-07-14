import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { FeedStatusPanel } from './FeedStatusPanel'

describe('FeedStatusPanel', () => {
  afterEach(cleanup)

  it('colors a failed feed source as critical and shows its error', () => {
    render(
      <FeedStatusPanel
        feedSources={[
          {
            id: 'feed-1',
            name: 'Threat feed',
            enabled: true,
            last_status: 'failed',
            last_error: 'upstream timeout',
            last_sync_at: '2026-07-14T00:00:00Z',
            last_run: null,
          },
        ]}
      />,
    )

    expect(screen.getByText('failed')).toHaveAttribute('data-severity', 'critical')
    expect(screen.getByText('Error: upstream timeout')).toBeInTheDocument()
  })

  it('renders an empty state without feed sources', () => {
    render(<FeedStatusPanel feedSources={[]} />)

    expect(screen.getByText('No feed sources are configured.')).toBeInTheDocument()
  })
})
