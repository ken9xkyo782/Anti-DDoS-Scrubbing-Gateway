import { describe, expect, it, vi } from 'vitest'

const { useQuery } = vi.hoisted(() => ({ useQuery: vi.fn() }))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQuery(...args),
}))

import { useNodeHealth, useNodeTelemetry } from './useNodeTelemetry'

describe('node telemetry hooks', () => {
  it('polls node telemetry and health every two seconds', () => {
    useQuery.mockReturnValue({})

    useNodeTelemetry()
    useNodeHealth()

    expect(useQuery).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ queryKey: ['node-telemetry'], refetchInterval: 2_000 }),
    )
    expect(useQuery).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ queryKey: ['node-health'], refetchInterval: 2_000 }),
    )
  })
})
