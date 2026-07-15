import { describe, expect, it, vi, beforeEach } from 'vitest'

const { useQuery } = vi.hoisted(() => ({
  useQuery: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQuery(...args),
}))

const { apiClient } = vi.hoisted(() => ({
  apiClient: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  apiClient,
}))

import { useJobs } from './useJobs'

describe('useJobs hook', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('queries all jobs when no filter is provided', () => {
    useQuery.mockReturnValue({})
    useJobs()
    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['jobs', 'all'],
      })
    )

    const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
    queryFn()
    expect(apiClient).toHaveBeenCalledWith('/jobs')
  })

  it('queries jobs with status filter when provided', () => {
    useQuery.mockReturnValue({})
    useJobs('queued')
    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ['jobs', 'queued'],
      })
    )

    const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
    queryFn()
    expect(apiClient).toHaveBeenCalledWith('/jobs?status=queued')
  })
})
