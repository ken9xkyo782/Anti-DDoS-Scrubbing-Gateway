import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { JobView, JobStatus } from '../../api/types'

export function useJobs(statusFilter?: JobStatus | null) {
  const path = statusFilter ? `/jobs?status=${statusFilter}` : '/jobs'
  return useQuery<JobView[]>({
    queryKey: ['jobs', statusFilter ?? 'all'],
    queryFn: () => apiClient<JobView[]>(path),
  })
}
