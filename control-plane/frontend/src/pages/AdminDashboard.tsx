import { BloomFpPanel } from '../components/BloomFpPanel'
import { CommittedHonoredPanel } from '../components/CommittedHonoredPanel'
import { FeedStatusPanel } from '../components/FeedStatusPanel'
import { NodeHealthPanel } from '../components/NodeHealthPanel'
import { NodeTelemetryPanel } from '../components/NodeTelemetryPanel'
import { TrendChart } from '../components/TrendChart'
import { useNodeHealth, useNodeTelemetry, useNodeTelemetryHistory } from '../hooks/useNodeTelemetry'

function NodeTrend() {
  const historyQuery = useNodeTelemetryHistory()

  return <TrendChart windows={historyQuery.data?.windows ?? []} />
}

export function AdminDashboard() {
  const telemetryQuery = useNodeTelemetry()
  const healthQuery = useNodeHealth()

  if (telemetryQuery.isPending || healthQuery.isPending) {
    return <p>Loading node telemetry…</p>
  }

  if (telemetryQuery.isError || healthQuery.isError) {
    return <p role="alert">Unable to load node telemetry.</p>
  }

  if (telemetryQuery.data === undefined || healthQuery.data === undefined) {
    return <p>No node telemetry data is available.</p>
  }

  return (
    <div>
      <h1>Admin dashboard</h1>
      <NodeHealthPanel health={healthQuery.data} />
      <BloomFpPanel bloomStats={healthQuery.data.bloom_stats ?? {}} />
      <CommittedHonoredPanel services={healthQuery.data.committed_services ?? []} />
      <FeedStatusPanel feedSources={healthQuery.data.feed_sources} />
      <NodeTelemetryPanel telemetry={telemetryQuery.data} />
      <NodeTrend />
    </div>
  )
}
