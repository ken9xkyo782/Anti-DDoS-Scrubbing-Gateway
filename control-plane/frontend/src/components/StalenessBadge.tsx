interface StalenessBadgeProps {
  hasData: boolean
  stale: boolean
  windowStart: string | null
}

export function StalenessBadge({
  hasData,
  stale,
  windowStart,
}: StalenessBadgeProps) {
  const isStale = !hasData || stale || windowStart === null

  return <p role="status">{isStale ? 'Stale telemetry' : 'Live telemetry'}</p>
}
