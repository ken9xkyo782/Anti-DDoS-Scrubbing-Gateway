import { useNodeHealth } from '../hooks/useNodeTelemetry'

const criticalStyle = {
  backgroundColor: '#b42318',
  color: '#ffffff',
  fontWeight: 700,
  margin: 0,
  padding: '0.75rem 1rem',
}

const maintenanceStyle = {
  backgroundColor: '#fef0c7',
  color: '#7a2e0e',
  fontWeight: 700,
  margin: 0,
  padding: '0.5rem 1rem',
}

export function NodeControlBanner() {
  const healthQuery = useNodeHealth()
  const bypassActive = healthQuery.data?.bypass.effective === true
  const maintenanceActive = healthQuery.data?.maintenance.effective === true

  if (!bypassActive && !maintenanceActive) {
    return null
  }

  return (
    <aside aria-label="Node control status">
      {bypassActive ? <p role="alert" style={criticalStyle}>BYPASS ACTIVE</p> : null}
      {maintenanceActive ? <p role="status" style={maintenanceStyle}>MAINTENANCE</p> : null}
    </aside>
  )
}
