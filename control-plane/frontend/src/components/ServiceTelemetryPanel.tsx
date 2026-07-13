import { CleanVsDropChart } from './CleanVsDropChart'
import { DropReasonChart } from './DropReasonChart'
import { RateTiles } from './RateTiles'
import { StalenessBadge } from './StalenessBadge'
import { useServiceTelemetry } from '../hooks/useServiceTelemetry'

interface ServiceTelemetryPanelProps {
  serviceId: string
}

export function ServiceTelemetryPanel({ serviceId }: ServiceTelemetryPanelProps) {
  const telemetryQuery = useServiceTelemetry(serviceId)

  if (telemetryQuery.isPending) {
    return <p>Loading telemetry…</p>
  }

  if (telemetryQuery.isError) {
    return <p role="alert">Unable to load telemetry for this service.</p>
  }

  const telemetry = telemetryQuery.data
  if (telemetry === undefined || !telemetry.has_data) {
    return (
      <section aria-labelledby="service-telemetry-heading">
        <h2 id="service-telemetry-heading">Service telemetry</h2>
        <p>No telemetry data is available for this service.</p>
        <StalenessBadge
          hasData={telemetry?.has_data ?? false}
          stale={telemetry?.stale ?? true}
          windowStart={telemetry?.window_start ?? null}
        />
      </section>
    )
  }

  return (
    <section aria-labelledby="service-telemetry-heading">
      <h2 id="service-telemetry-heading">Service telemetry</h2>
      <StalenessBadge
        hasData={telemetry.has_data}
        stale={telemetry.stale}
        windowStart={telemetry.window_start}
      />
      <RateTiles pps={telemetry.pps} bps={telemetry.bps} />
      <CleanVsDropChart cleanPackets={telemetry.clean_pkts} droppedPackets={telemetry.drop_pkts} />
      <DropReasonChart dropByReason={telemetry.drop_by_reason} />
    </section>
  )
}
