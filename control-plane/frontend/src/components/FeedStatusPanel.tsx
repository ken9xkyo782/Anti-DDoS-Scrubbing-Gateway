import type { FeedSource } from '../hooks/useNodeTelemetry'
import type { Severity } from '../theme/thresholds'
import styles from './dashboard.module.css'

interface FeedStatusPanelProps {
  feedSources: FeedSource[]
}

const BADGE_TONE: Record<Severity, string> = {
  ok: styles.badgeOk,
  warning: styles.badgeWarn,
  critical: styles.badgeCrit,
}

function feedSeverity(source: FeedSource): Severity {
  if (!source.enabled) {
    return 'ok'
  }
  if (source.last_status === 'failed') {
    return 'critical'
  }
  if (source.last_status === null) {
    return 'warning'
  }
  return 'ok'
}

function statusLabel(source: FeedSource): string {
  if (!source.enabled) {
    return 'disabled'
  }
  return source.last_status ?? 'not synced'
}

export function FeedStatusPanel({ feedSources }: FeedStatusPanelProps) {
  return (
    <section aria-labelledby="feed-status-heading" className={styles.section}>
      <h3 id="feed-status-heading" className={styles.title}>
        Feed sync status
      </h3>
      {feedSources.length === 0 ? (
        <p className={styles.empty}>No feed sources are configured.</p>
      ) : (
        <ul className={styles.statusList}>
          {feedSources.map((source) => {
            const severity = feedSeverity(source)
            return (
              <li key={source.id} className={styles.statusItem}>
                <div className={styles.statusRow}>
                  <span className={styles.statusName}>{source.name}</span>
                  <span
                    className={`${styles.badge} ${BADGE_TONE[severity]}`}
                    data-severity={severity}
                  >
                    {statusLabel(source)}
                  </span>
                </div>
                {source.last_error ? (
                  <p className={styles.statusError}>Error: {source.last_error}</p>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
