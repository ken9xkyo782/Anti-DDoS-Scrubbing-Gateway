import React from 'react'
import { Badge } from '../Badge/Badge'

export interface StatusBadgeProps {
  status: string
  className?: string
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, className = '' }) => {
  let variant: 'default' | 'success' | 'warning' | 'danger' | 'info' = 'default'
  const normalized = status.toLowerCase().trim()

  if (normalized === 'active') {
    variant = 'success'
  } else if (normalized === 'failed') {
    variant = 'danger'
  } else if (normalized === 'applying') {
    variant = 'info'
  } else if (normalized === 'pending' || normalized === 'queued') {
    variant = 'warning'
  }

  const label = status.charAt(0).toUpperCase() + status.slice(1)

  return (
    <Badge variant={variant} className={className}>
      {label}
    </Badge>
  )
}
