import type { NodeHealth } from '../hooks/useNodeTelemetry'
import { StalenessBadge } from './StalenessBadge'
import { ThroughputGauge } from './ThroughputGauge'
import { XdpModeFlag } from './XdpModeFlag'

interface NodeHealthPanelProps {
  health: NodeHealth
}

export function NodeHealthPanel({ health }: NodeHealthPanelProps) {
  return (
    <section aria-labelledby="node-health-heading">
      <h2 id="node-health-heading">Node health</h2>
      <XdpModeFlag mode={health.xdp_mode} />
      <StalenessBadge
        hasData={health.has_data}
        stale={health.stale}
        windowStart={health.window_start}
      />
      {!health.has_data ? (
        <p>No node health data is available.</p>
      ) : (
        <>
          <dl>
            <div>
              <dt>Active slot</dt>
              <dd>{health.active_slot ?? 'Unavailable'}</dd>
            </div>
            <div>
              <dt>Map version</dt>
              <dd>{health.map_version ?? 'Unavailable'}</dd>
            </div>
            <div>
              <dt>Map errors</dt>
              <dd>{health.map_error_count.toLocaleString()}</dd>
            </div>
          </dl>
          <ThroughputGauge
            cleanBps={health.node_clean_bps}
            capacityBps={health.node_capacity_bps}
          />
        </>
      )}
      <section aria-labelledby="job-backlog-heading">
        <h3 id="job-backlog-heading">Job backlog</h3>
        <p>Queued jobs: {health.job_backlog.queued}</p>
        <p>Applying jobs: {health.job_backlog.applying}</p>
      </section>
      <section aria-labelledby="feed-status-heading">
        <h3 id="feed-status-heading">Feed sources</h3>
        {health.feed_sources.length === 0 ? (
          <p>No feed sources configured.</p>
        ) : (
          <ul>
            {health.feed_sources.map((source) => (
              <li key={source.id}>
                {source.name}: {source.enabled ? source.last_status ?? 'not synced' : 'disabled'}
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  )
}
