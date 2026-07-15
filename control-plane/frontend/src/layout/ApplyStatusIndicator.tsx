import React, { useCallback, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Spinner } from '../ui'
import styles from './ApplyStatusIndicator.module.css'

export const ApplyStatusIndicator: React.FC = () => {
  const queryClient = useQueryClient()

  const getCount = useCallback(() => {
    const applyStatusQueries = queryClient.getQueryCache().findAll({
      queryKey: ['apply-status'],
    })
    const servicesQueries = queryClient.getQueryCache().findAll({
      queryKey: ['services'],
    })

    const inFlightServiceIds = new Set<string>()

    for (const query of applyStatusQueries) {
      const data = query.state.data as { apply_status?: string; service_id?: string } | undefined
      if (data && ['pending', 'queued', 'applying'].includes(data.apply_status || '')) {
        if (data.service_id) {
          inFlightServiceIds.add(data.service_id)
        }
      }
    }

    for (const query of servicesQueries) {
      const data = query.state.data
      if (Array.isArray(data)) {
        for (const service of data) {
          const s = service as { id?: string; apply_status?: string } | undefined
          if (s && ['pending', 'queued', 'applying'].includes(s.apply_status || '')) {
            if (s.id) {
              inFlightServiceIds.add(s.id)
            }
          }
        }
      } else if (data && typeof data === 'object') {
        const s = data as { id?: string; apply_status?: string }
        if (s && ['pending', 'queued', 'applying'].includes(s.apply_status || '')) {
          if (s.id) {
            inFlightServiceIds.add(s.id)
          }
        }
      }
    }

    return inFlightServiceIds.size
  }, [queryClient])

  const [inFlightCount, setInFlightCount] = useState(() => getCount())

  useEffect(() => {
    const unsubscribe = queryClient.getQueryCache().subscribe(() => {
      setInFlightCount(getCount())
    })

    return unsubscribe
  }, [queryClient, getCount])

  if (inFlightCount === 0) {
    return null
  }

  return (
    <div className={styles.indicator} role="status" aria-live="polite">
      <Spinner size="sm" />
      <span className={styles.text}>
        Applying {inFlightCount} configuration{inFlightCount > 1 ? 's' : ''}...
      </span>
    </div>
  )
}
