import type { NodeTelemetry } from '../hooks/useNodeTelemetry'
import { CleanVsDropChart } from './CleanVsDropChart'
import { DropReasonChart } from './DropReasonChart'
import { RateTiles } from './RateTiles'
import { StalenessBadge } from './StalenessBadge'

interface NodeTelemetryPanelProps {
  telemetry: NodeTelemetry
}

export function NodeTelemetryPanel({ telemetry }: NodeTelemetryPanelProps) {
  return (
    <section aria-labelledby="node-telemetry-heading">
      <h2 id="node-telemetry-heading">Node telemetry</h2>
      <StalenessBadge
        hasData={telemetry.has_data}
        stale={telemetry.stale}
        windowStart={telemetry.window_start}
      />
      {!telemetry.has_data ? (
        <p>No node telemetry data is available.</p>
      ) : (
        <>
          <RateTiles pps={telemetry.pps} bps={telemetry.bps} />
          <CleanVsDropChart
            cleanPackets={telemetry.clean_pkts}
            droppedPackets={telemetry.drop_pkts}
          />
          <DropReasonChart dropByReason={telemetry.drop_by_reason} />
        </>
      )}
    </section>
  )
}
