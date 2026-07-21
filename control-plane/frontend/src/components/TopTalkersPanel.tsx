import type { TopPort, TopSource } from '../hooks/useServiceTelemetry'
import styles from './dashboard.module.css'

interface TopTalkersPanelProps {
  topDstPorts: TopPort[]
  topSrc: TopSource[]
}

export function TopTalkersPanel({ topDstPorts, topSrc }: TopTalkersPanelProps) {
  return (
    <section aria-labelledby="top-talkers-heading" className={styles.section}>
      <h3 id="top-talkers-heading" className={styles.title}>
        Top talkers
      </h3>
      <p className={styles.hint}>
        Sampled from dropped-packet events — approximate, not exact counts.
      </p>
      <div className={styles.columns}>
        <div className={styles.colStack}>
          <h4 className={styles.subtitle}>Top destination ports</h4>
          {topDstPorts.length === 0 ? (
            <p className={styles.empty}>No sampled destination ports in this window.</p>
          ) : (
            <ol className={styles.rankedList}>
              {topDstPorts.map((entry) => (
                <li key={entry.port}>
                  Port {entry.port}: {entry.count.toLocaleString()}
                </li>
              ))}
            </ol>
          )}
        </div>
        <div className={styles.colStack}>
          <h4 className={styles.subtitle}>Top source addresses</h4>
          <p className={styles.hint}>Source IPs are sampled and not anonymized (pilot posture).</p>
          {topSrc.length === 0 ? (
            <p className={styles.empty}>No sampled source addresses in this window.</p>
          ) : (
            <ol className={styles.rankedList}>
              {topSrc.map((entry) => (
                <li key={entry.ip}>
                  {entry.ip}: {entry.count.toLocaleString()}
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </section>
  )
}
