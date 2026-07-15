import { severityColor, throughputSeverity } from '../theme/thresholds'
import styles from './dashboard.module.css'

interface ThroughputGaugeProps {
  cleanBps: number
  capacityBps: number
}

export function ThroughputGauge({ cleanBps, capacityBps }: ThroughputGaugeProps) {
  const percentage = capacityBps > 0 ? Math.min((cleanBps / capacityBps) * 100, 100) : 0
  const severity = throughputSeverity(cleanBps, capacityBps)

  return (
    <section aria-label="Clean throughput" className={styles.gaugeWrap}>
      <h3 className={styles.subtitle}>Clean throughput</h3>
      <progress className={styles.gauge} max="100" value={percentage} />
      <div className={styles.gaugeMeta}>
        <p
          className={styles.gaugePct}
          data-severity={severity}
          style={{ color: severityColor(severity) }}
        >
          {percentage.toFixed(1)}% of capacity
        </p>
        <p className={styles.gaugeAbs}>
          {cleanBps.toLocaleString()} bps of {capacityBps.toLocaleString()} bps
        </p>
      </div>
    </section>
  )
}
