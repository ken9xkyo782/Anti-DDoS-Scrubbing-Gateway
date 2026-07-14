import { describe, expect, it, vi } from 'vitest'

const { useQuery } = vi.hoisted(() => ({ useQuery: vi.fn() }))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQuery(...args),
}))

import { useBillingUsage } from './useBillingUsage'

describe('useBillingUsage', () => {
  it('polls the tenant-scoped billing usage endpoint every thirty seconds', () => {
    useQuery.mockReturnValue({})

    useBillingUsage()

    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['billing-usage'],
        refetchInterval: 30_000,
      }),
    )
  })
})
