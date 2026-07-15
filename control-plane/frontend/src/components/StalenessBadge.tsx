import styles from './dashboard.module.css'

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

  return (
    <p
      role="status"
      className={`${styles.badge} ${isStale ? styles.badgeWarn : styles.badgeOk}`}
    >
      <span className={styles.dot} aria-hidden="true" />
      {isStale ? 'Stale telemetry' : 'Live telemetry'}
    </p>
  )
}
