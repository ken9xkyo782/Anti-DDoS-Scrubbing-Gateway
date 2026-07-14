import { bloomFpSeverity, severityColor } from '../theme/thresholds'

interface BloomFpPanelProps {
  bloomStats: Record<string, number>
}

const BLOOM_LABELS: Record<string, string> = {
  whitelist: 'Whitelist bloom',
  global_blacklist: 'Global blacklist bloom',
  service_blacklist: 'Service blacklist bloom',
}

export function BloomFpPanel({ bloomStats }: BloomFpPanelProps) {
  const entries = Object.entries(bloomStats)

  return (
    <section aria-labelledby="bloom-fp-heading">
      <h3 id="bloom-fp-heading">Bloom false positives</h3>
      {entries.length === 0 ? (
        <p>No bloom filter statistics are available.</p>
      ) : (
        <dl>
          {entries.map(([name, count]) => {
            const severity = bloomFpSeverity(count)
            return (
              <div key={name}>
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
