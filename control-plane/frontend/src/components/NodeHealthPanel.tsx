import type { NodeHealth } from '../hooks/useNodeTelemetry'
import { mapErrorSeverity, severityColor } from '../theme/thresholds'
import styles from './dashboard.module.css'
import { ThroughputGauge } from './ThroughputGauge'
import { XdpModeFlag } from './XdpModeFlag'

interface NodeHealthPanelProps {
  health: NodeHealth
}

export function NodeHealthPanel({ health }: NodeHealthPanelProps) {
  const mapErrSeverity = mapErrorSeverity(health.map_error_count)

  return (
    <section aria-labelledby="node-health-heading" className={styles.section}>
      <div className={styles.panelHead}>
        <h2 id="node-health-heading" className={styles.title}>
          Node health
        </h2>
        <XdpModeFlag mode={health.xdp_mode} />
      </div>
      {!health.has_data ? (
        <p className={styles.empty}>No node health data is available.</p>
      ) : (
        <>
          <dl className={styles.tiles}>
            <div className={styles.tile}>
              <dt className={styles.tileLabel}>Active slot</dt>
              <dd className={styles.tileValue}>{health.active_slot ?? 'Unavailable'}</dd>
            </div>
            <div className={styles.tile}>
              <dt className={styles.tileLabel}>Map version</dt>
              <dd className={styles.tileValue}>{health.map_version ?? 'Unavailable'}</dd>
            </div>
            <div className={styles.tile}>
              <dt className={styles.tileLabel}>Map errors</dt>
              <dd
                className={styles.tileValue}
                data-severity={mapErrSeverity}
                style={{ color: severityColor(mapErrSeverity) }}
              >
                {health.map_error_count.toLocaleString()}
              </dd>
            </div>
          </dl>
          <ThroughputGauge
            cleanBps={health.node_clean_bps}
            capacityBps={health.node_capacity_bps}
          />
        </>
      )}
      <section aria-labelledby="job-backlog-heading" className={styles.colStack}>
        <h3 id="job-backlog-heading" className={styles.subtitle}>
          Job backlog
        </h3>
        <div className={styles.badgeRow}>
          <p className={`${styles.badge} ${styles.badgeNeutral}`}>
            Queued jobs: {health.job_backlog.queued}
          </p>
          <p className={`${styles.badge} ${styles.badgeNeutral}`}>
            Applying jobs: {health.job_backlog.applying}
          </p>
        </div>
      </section>
    </section>
  )
}
