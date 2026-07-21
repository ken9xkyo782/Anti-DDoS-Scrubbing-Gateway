import { committedSeverity, severityColor } from '../theme/thresholds'
import styles from './dashboard.module.css'

export interface CommittedServiceRow {
  service_id: string
  observed_clean_bps: number
  committed_clean_bps: number
  honored: boolean | null
}

interface CommittedHonoredPanelProps {
  services: CommittedServiceRow[]
}

function gbps(bps: number): string {
  return `${(bps / 1_000_000_000).toFixed(2)} Gbps`
}

function honoredLabel(honored: boolean | null): string {
  if (honored === null) {
    return 'No data'
  }
  return honored ? 'Honored' : 'Breached'
}

export function CommittedHonoredPanel({ services }: CommittedHonoredPanelProps) {
  return (
    <section aria-labelledby="committed-honored-heading" className={styles.section}>
      <h3 id="committed-honored-heading" className={styles.title}>
        Committed throughput honored
      </h3>
      {services.length === 0 ? (
        <p className={styles.empty}>No services have a committed plan.</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Service</th>
                <th scope="col">Observed clean</th>
                <th scope="col">Committed</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {services.map((service) => {
                const severity = committedSeverity(service.honored)
                return (
                  <tr key={service.service_id}>
                    <td>{service.service_id}</td>
                    <td>{gbps(service.observed_clean_bps)}</td>
                    <td>{gbps(service.committed_clean_bps)}</td>
                    <td data-severity={severity} style={{ color: severityColor(severity) }}>
                      {honoredLabel(service.honored)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
