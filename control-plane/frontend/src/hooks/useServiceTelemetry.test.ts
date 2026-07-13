import { describe, expect, it, vi } from 'vitest'

const { useQuery } = vi.hoisted(() => ({ useQuery: vi.fn() }))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQuery(...args),
}))

import { useServiceTelemetry } from './useServiceTelemetry'

describe('useServiceTelemetry', () => {
  it('polls the selected service telemetry endpoint every two seconds', () => {
    useQuery.mockReturnValue({})

    useServiceTelemetry('service-1')

    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['service-telemetry', 'service-1'],
        refetchInterval: 2_000,
        enabled: true,
      }),
    )
  })
})
