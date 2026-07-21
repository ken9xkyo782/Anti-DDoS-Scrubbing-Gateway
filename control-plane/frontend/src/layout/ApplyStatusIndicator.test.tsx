import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApplyStatusIndicator } from './ApplyStatusIndicator'

describe('ApplyStatusIndicator', () => {
  afterEach(cleanup)

  it('renders nothing when there are no in-flight applies', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { gcTime: 0 } },
    })

    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <ApplyStatusIndicator />
      </QueryClientProvider>
    )

    expect(container.firstChild).toBeNull()
  })

  it('renders the count of in-flight applies from the query cache', () => {
    const queryClient = new QueryClient()

    // Seed the cache with a services query that has a service in 'pending' status
    queryClient.setQueryData(['services'], [
      { id: 'srv-1', apply_status: 'pending' },
      { id: 'srv-2', apply_status: 'active' }, // terminal, should not count
    ])

    // Seed the cache with an individual apply-status query in 'applying' status
    queryClient.setQueryData(['apply-status', 'srv-3'], {
      service_id: 'srv-3',
      apply_status: 'applying',
    })

    // Seed the cache with another apply-status query that is duplicate/terminal
    queryClient.setQueryData(['apply-status', 'srv-1'], {
      service_id: 'srv-1',
      apply_status: 'pending',
    })

    render(
      <QueryClientProvider client={queryClient}>
        <ApplyStatusIndicator />
      </QueryClientProvider>
    )

    // Total in-flight should be 2 unique services: 'srv-1' (pending) and 'srv-3' (applying).
    expect(screen.getByText('Applying 2 configurations...')).toBeInTheDocument()
  })
})
