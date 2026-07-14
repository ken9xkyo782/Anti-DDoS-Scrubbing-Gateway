import { type AlertRecord, useAlerts } from '../hooks/useAlerts'
import { severityColor } from '../theme/thresholds'

function alertColor(severity: AlertRecord['severity']) {
  return severityColor(severity === 'info' ? 'ok' : severity)
}

function timestamp(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

function deliveryState(alert: AlertRecord) {
  if (alert.notifications.length === 0) {
    return 'not queued'
  }
  return alert.notifications.map((notification) => notification.state).join(', ')
}

export function AlertsPanel() {
  const alertsQuery = useAlerts()

  if (alertsQuery.isPending) {
    return <p>Loading alerts…</p>
  }
  if (alertsQuery.isError) {
    return <p role="alert">Unable to load alerts.</p>
  }
  if (alertsQuery.data === undefined || !alertsQuery.data.has_data) {
    return (
      <section aria-labelledby="alerts-heading">
        <h1 id="alerts-heading">Alerts</h1>
        <p>No alerts are active or recorded yet.</p>
      </section>
    )
  }

  return (
    <section aria-labelledby="alerts-heading">
      <h1 id="alerts-heading">Alerts</h1>
      <table>
        <thead>
          <tr>
            <th scope="col">Rule</th>
            <th scope="col">Severity</th>
            <th scope="col">Scope</th>
            <th scope="col">State</th>
            <th scope="col">Fired</th>
            <th scope="col">Resolved</th>
            <th scope="col">Delivery</th>
          </tr>
        </thead>
        <tbody>
          {alertsQuery.data.alerts.map((alert) => (
            <tr key={alert.id}>
              <td>{alert.rule_key}{alert.service_name ? ` — ${alert.service_name}` : ''}</td>
              <td style={{ color: alertColor(alert.severity) }}>{alert.severity}</td>
              <td>{alert.scope}</td>
              <td>{alert.state}</td>
              <td>{timestamp(alert.fired_at)}</td>
              <td>{timestamp(alert.resolved_at)}</td>
              <td>{deliveryState(alert)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
