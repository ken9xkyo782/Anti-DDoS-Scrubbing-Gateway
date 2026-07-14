import { useAuth } from '../auth/AuthContext'
import { type BillingUsage, useBillingUsage } from '../hooks/useBillingUsage'

interface UsageTotal {
  billed: number
  committed: number
  overage: number
}

function formatGbps(value: string | number) {
  return `${Number(value).toFixed(2)} Gbps`
}

function periodLabel(periodStart: string) {
  return periodStart.slice(0, 7)
}

function totalUsage(usage: BillingUsage[]): UsageTotal {
  return usage.reduce(
    (total, item) => ({
      billed: total.billed + Number(item.billed_gbps),
      committed: total.committed + Number(item.committed_clean_gbps),
      overage: total.overage + Number(item.overage_gbps),
    }),
    { billed: 0, committed: 0, overage: 0 },
  )
}

function CurrentServiceUsage({ usage }: { usage: BillingUsage[] }) {
  if (usage.length === 0) {
    return <p>No current billing usage is available.</p>
  }

  return (
    <ul>
      {usage.map((item) => (
        <li key={`${item.service_id}-${item.period_start}`}>
          <h3>{item.service_name}</h3>
          <dl>
            <div>
              <dt>Billed</dt>
              <dd>{formatGbps(item.billed_gbps)}</dd>
            </div>
            <div>
              <dt>Committed</dt>
              <dd>{formatGbps(item.committed_clean_gbps)}</dd>
            </div>
          </dl>
          {Number(item.overage_gbps) > 0 ? <p>Overage: {formatGbps(item.overage_gbps)}</p> : null}
          {item.provisional ? <p role="status">Provisional</p> : null}
        </li>
      ))}
    </ul>
  )
}

function TenantUsageSummary({ usage }: { usage: BillingUsage[] }) {
  const byTenant = new Map<string, BillingUsage[]>()

  for (const item of usage) {
    const tenantId = item.tenant_id ?? 'Unknown tenant'
    byTenant.set(tenantId, [...(byTenant.get(tenantId) ?? []), item])
  }

  return (
    <section aria-labelledby="tenant-usage-heading">
      <h2 id="tenant-usage-heading">Tenant-wide current usage</h2>
      <ul>
        {[...byTenant.entries()].map(([tenantId, tenantUsage]) => {
          const total = totalUsage(tenantUsage)

          return (
            <li key={tenantId}>
              <h3>{tenantId}</h3>
              <p>Billed: {formatGbps(total.billed)}</p>
              <p>Committed: {formatGbps(total.committed)}</p>
              {total.overage > 0 ? <p>Overage: {formatGbps(total.overage)}</p> : null}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function FinalizedPeriods({ usage }: { usage: BillingUsage[] }) {
  return (
    <section aria-labelledby="finalized-periods-heading">
      <h2 id="finalized-periods-heading">Finalized periods</h2>
      {usage.length === 0 ? (
        <p>No finalized billing periods are available.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Service</th>
              <th scope="col">Period</th>
              <th scope="col">Billed</th>
              <th scope="col">Committed</th>
              <th scope="col">Overage</th>
            </tr>
          </thead>
          <tbody>
            {usage.map((item) => (
              <tr key={`${item.service_id}-${item.period_start}`}>
                <td>{item.service_name} — finalized</td>
                <td>{periodLabel(item.period_start)}</td>
                <td>{formatGbps(item.billed_gbps)}</td>
                <td>{formatGbps(item.committed_clean_gbps)}</td>
                <td>{formatGbps(item.overage_gbps)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export function BillingPanel() {
  const { principal } = useAuth()
  const usageQuery = useBillingUsage()

  if (usageQuery.isPending) {
    return <p>Loading billing usage…</p>
  }

  if (usageQuery.isError) {
    return <p role="alert">Unable to load billing usage.</p>
  }

  const response = usageQuery.data
  if (response === undefined || !response.has_data) {
    return (
      <section aria-labelledby="billing-heading">
        <h1 id="billing-heading">Billing</h1>
        <p>No billing usage is available yet.</p>
      </section>
    )
  }

  const currentUsage = response.usage.filter((item) => item.status === 'open')
  const finalizedUsage = response.usage.filter((item) => item.status === 'final')
  const isAdmin = principal?.role === 'admin'
  const nodeTotal = totalUsage(currentUsage)

  return (
    <div>
      <h1>Billing</h1>
      {isAdmin ? (
        <>
          <section aria-labelledby="node-billing-heading">
            <h2 id="node-billing-heading">Node-wide current usage</h2>
            <dl>
              <div>
                <dt>Billed</dt>
                <dd>{formatGbps(nodeTotal.billed)}</dd>
              </div>
              <div>
                <dt>Committed</dt>
                <dd>{formatGbps(nodeTotal.committed)}</dd>
              </div>
            </dl>
            {nodeTotal.overage > 0 ? <p>Overage: {formatGbps(nodeTotal.overage)}</p> : null}
          </section>
          <TenantUsageSummary usage={currentUsage} />
        </>
      ) : null}
      <section aria-labelledby="current-service-usage-heading">
        <h2 id="current-service-usage-heading">Current service usage</h2>
        <CurrentServiceUsage usage={currentUsage} />
      </section>
      <FinalizedPeriods usage={finalizedUsage} />
    </div>
  )
}
