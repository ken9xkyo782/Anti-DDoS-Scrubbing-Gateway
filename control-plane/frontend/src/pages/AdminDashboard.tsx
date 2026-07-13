import { NodeHealthPanel } from '../components/NodeHealthPanel'
import { NodeTelemetryPanel } from '../components/NodeTelemetryPanel'
import { useNodeHealth, useNodeTelemetry } from '../hooks/useNodeTelemetry'

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
      <NodeTelemetryPanel telemetry={telemetryQuery.data} />
    </div>
  )
}
