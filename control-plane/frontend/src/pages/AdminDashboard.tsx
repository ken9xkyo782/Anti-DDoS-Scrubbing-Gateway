import { PageHeader } from '../ui'
import { BloomFpPanel } from '../components/BloomFpPanel'
import { CleanVsDropChart } from '../components/CleanVsDropChart'
import { CommittedHonoredPanel } from '../components/CommittedHonoredPanel'
import { DropReasonChart } from '../components/DropReasonChart'
import { FeedStatusPanel } from '../components/FeedStatusPanel'
import { NodeHealthPanel } from '../components/NodeHealthPanel'
import { StalenessBadge } from '../components/StalenessBadge'
import { TopTalkersPanel } from '../components/TopTalkersPanel'
import { TrendChart } from '../components/TrendChart'
import { useNodeHealth, useNodeTelemetry, useNodeTelemetryHistory } from '../hooks/useNodeTelemetry'
import styles from './AdminDashboard.module.css'

function formatBits(bps: number): string {
  if (bps >= 1_000_000_000) return `${(bps / 1_000_000_000).toFixed(2)} Gbps`
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} Mbps`
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(1)} Kbps`
  return `${bps.toLocaleString()} bps`
}

function formatPackets(pps: number): string {
  if (pps >= 1_000_000) return `${(pps / 1_000_000).toFixed(2)} Mpps`
  if (pps >= 1_000) return `${(pps / 1_000).toFixed(1)} Kpps`
  return `${pps.toLocaleString()} pps`
}

function NodeTrend() {
  const historyQuery = useNodeTelemetryHistory()

  return <TrendChart windows={historyQuery.data?.windows ?? []} />
}

export function AdminDashboard() {
  const telemetryQuery = useNodeTelemetry()
  const healthQuery = useNodeHealth()

  if (telemetryQuery.isPending || healthQuery.isPending) {
    return (
      <div className={styles.page}>
        <PageHeader title="Node telemetry" />
        <div className={styles.state}>
          <p>Loading node telemetry…</p>
        </div>
      </div>
    )
  }

  if (telemetryQuery.isError || healthQuery.isError) {
    return (
      <div className={styles.page}>
        <PageHeader title="Node telemetry" />
        <div className={`${styles.state} ${styles.stateError}`} role="alert">
          <p>Unable to load node telemetry.</p>
        </div>
      </div>
    )
  }

  if (telemetryQuery.data === undefined || healthQuery.data === undefined) {
    return (
      <div className={styles.page}>
        <PageHeader title="Node telemetry" />
        <div className={styles.state}>
          <p>No node telemetry data is available.</p>
        </div>
      </div>
    )
  }

  const telemetry = telemetryQuery.data
  const health = healthQuery.data

  const throughputPct =
    health.node_capacity_bps > 0
      ? Math.min((health.node_clean_bps / health.node_capacity_bps) * 100, 100)
      : 0
  const totalPackets = telemetry.clean_pkts + telemetry.drop_pkts
  const dropRatio = totalPackets > 0 ? (telemetry.drop_pkts / totalPackets) * 100 : 0

  return (
    <div className={styles.page}>
      <PageHeader
        title="Node telemetry"
        description="Live scrubbing throughput, drop analysis, and node health for the gateway."
        actions={
          <StalenessBadge
            hasData={telemetry.has_data}
            stale={telemetry.stale}
            windowStart={telemetry.window_start}
          />
        }
      />

      <div className={styles.kpis}>
        <div className={styles.kpi}>
          <span className={styles.kpiLabel}>Clean throughput</span>
          <span className={styles.kpiValue}>{throughputPct.toFixed(1)}%</span>
          <span className={styles.kpiSub}>
            {formatBits(health.node_clean_bps)} of {formatBits(health.node_capacity_bps)}
          </span>
        </div>
        <div className={styles.kpi}>
          <span className={styles.kpiLabel}>Packets / sec</span>
          <span className={styles.kpiValue}>{formatPackets(telemetry.pps)}</span>
          <span className={styles.kpiSub}>{telemetry.clean_pkts.toLocaleString()} clean in window</span>
        </div>
        <div className={styles.kpi}>
          <span className={styles.kpiLabel}>Bits / sec</span>
          <span className={styles.kpiValue}>{formatBits(telemetry.bps)}</span>
          <span className={styles.kpiSub}>scrubbed traffic</span>
        </div>
        <div className={styles.kpi}>
          <span className={styles.kpiLabel}>Drop ratio</span>
          <span className={styles.kpiValue}>{dropRatio.toFixed(2)}%</span>
          <span className={styles.kpiSub}>{telemetry.drop_pkts.toLocaleString()} pkts dropped</span>
        </div>
      </div>

      <div className={styles.grid}>
        <div className={styles.card}>
          <NodeHealthPanel health={health} />
        </div>
        <div className={styles.card}>
          <BloomFpPanel bloomStats={health.bloom_stats ?? {}} />
        </div>
        <div className={styles.card}>
          <FeedStatusPanel feedSources={health.feed_sources} />
        </div>
        <div className={styles.card}>
          <CleanVsDropChart cleanPackets={telemetry.clean_pkts} droppedPackets={telemetry.drop_pkts} />
        </div>
        <div className={styles.card}>
          <DropReasonChart dropByReason={telemetry.drop_by_reason} />
        </div>
        <div className={styles.card}>
          <TopTalkersPanel
            topDstPorts={telemetry.top_dst_ports ?? []}
            topSrc={telemetry.top_src ?? []}
          />
        </div>
        <div className={`${styles.card} ${styles.span12}`}>
          <CommittedHonoredPanel services={health.committed_services ?? []} />
        </div>
        <div className={`${styles.card} ${styles.span12}`}>
          <NodeTrend />
        </div>
      </div>
    </div>
  )
}
