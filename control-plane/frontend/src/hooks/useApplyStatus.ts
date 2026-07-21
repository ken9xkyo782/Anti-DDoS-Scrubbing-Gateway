/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../api/client'
import type { ApplyStatusView } from '../api/types'

export function useApplyStatus(
  serviceId: string | null,
  options?: { enabled?: boolean }
) {
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [takingLonger, setTakingLonger] = useState(false)

  const isEnabled = serviceId !== null && (options?.enabled ?? true)

  const statusQuery = useQuery<ApplyStatusView>({
    queryKey: ['apply-status', serviceId],
    queryFn: () => apiClient<ApplyStatusView>(`/services/${serviceId}/apply-status`),
    enabled: isEnabled,
    refetchInterval: (query) => {
      if (!isEnabled) return false
      
      const data = query.state.data as ApplyStatusView | undefined
      const status = data?.apply_status
      const isTerminal = !status || status === 'active' || status === 'failed'
      if (isTerminal) return false

      return takingLonger ? 5_000 : 1_000
    },
  })

  // Safely guard data availability
  const currentStatus = statusQuery?.data?.apply_status
  const isTerminal = !currentStatus || currentStatus === 'active' || currentStatus === 'failed'

  // Manage startedAt when status changes
  useEffect(() => {
    if (!isEnabled || isTerminal) {
      setStartedAt(null)
      setTakingLonger(false)
      return
    }

    if (startedAt === null) {
      setStartedAt(Date.now())
    }
  }, [currentStatus, isTerminal, isEnabled, startedAt])

  // Manage takingLonger based on startedAt timer
  useEffect(() => {
    if (startedAt === null) {
      setTakingLonger(false)
      return
    }

    const elapsed = Date.now() - startedAt
    const remaining = 30_000 - elapsed

    if (remaining <= 0) {
      setTakingLonger(true)
      return
    }

    setTakingLonger(false)

    const timer = setTimeout(() => {
      setTakingLonger(true)
    }, remaining)

    return () => clearTimeout(timer)
  }, [startedAt])

  return {
    ...statusQuery,
    takingLonger,
  }
}
