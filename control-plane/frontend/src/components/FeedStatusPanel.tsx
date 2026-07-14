import type { FeedSource } from '../hooks/useNodeTelemetry'
import type { Severity } from '../theme/thresholds'
import { severityColor } from '../theme/thresholds'

interface FeedStatusPanelProps {
  feedSources: FeedSource[]
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
    <section aria-labelledby="feed-status-heading">
      <h3 id="feed-status-heading">Feed sync status</h3>
      {feedSources.length === 0 ? (
        <p>No feed sources are configured.</p>
      ) : (
        <ul>
          {feedSources.map((source) => {
            const severity = feedSeverity(source)
            return (
              <li key={source.id}>
                <span>{source.name}: </span>
                <span data-severity={severity} style={{ color: severityColor(severity) }}>
                  {statusLabel(source)}
                </span>
                {source.last_error ? <p>Error: {source.last_error}</p> : null}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
