import { bloomFpSeverity, severityColor } from '../theme/thresholds'
import styles from './dashboard.module.css'

interface BloomFpPanelProps {
  bloomStats: Record<string, number>
}

const BLOOM_LABELS: Record<string, string> = {
  whitelist: 'Whitelist bloom',
  global_blacklist: 'Global blacklist bloom',
  // Retained for historical telemetry snapshots loaded from older data-plane nodes
  service_blacklist: 'Service blacklist bloom',
}

export function BloomFpPanel({ bloomStats }: BloomFpPanelProps) {
  const entries = Object.entries(bloomStats)

  return (
    <section aria-labelledby="bloom-fp-heading" className={styles.section}>
      <h3 id="bloom-fp-heading" className={styles.title}>
        Bloom false positives
      </h3>
      {entries.length === 0 ? (
        <p className={styles.empty}>No bloom filter statistics are available.</p>
      ) : (
        <dl className={styles.defList}>
          {entries.map(([name, count]) => {
            const severity = bloomFpSeverity(count)
            return (
              <div key={name} className={styles.defRow}>
                <dt>{BLOOM_LABELS[name] ?? name}</dt>
                <dd data-severity={severity} style={{ color: severityColor(severity) }}>
                  {count.toLocaleString()}
                </dd>
              </div>
            )
          })}
        </dl>
      )}
    </section>
  )
}
