export type Severity = 'ok' | 'warning' | 'critical'

const SEVERITY_COLORS: Record<Severity, string> = {
  ok: 'var(--color-ok)',
  warning: 'var(--color-warning)',
  critical: 'var(--color-critical)',
}

// TDD §9.1 metric thresholds. Coloring is display-only: the dashboard never
// fires an alert (alerting is M6). Each helper maps a metric to a severity so
// panels color a value consistently.

export function severityColor(severity: Severity): string {
  return SEVERITY_COLORS[severity]
}

// `map_error` counter — any nonzero value is a critical data-plane fault.
export function mapErrorSeverity(count: number): Severity {
  return count > 0 ? 'critical' : 'ok'
}

// Node clean throughput approaching capacity signals congestion.
export const THROUGHPUT_WARNING_RATIO = 0.9

export function throughputSeverity(cleanBps: number, capacityBps: number): Severity {
  if (capacityBps <= 0) {
    return 'ok'
  }
  const ratio = cleanBps / capacityBps
  if (ratio >= 1) {
    return 'critical'
  }
  if (ratio >= THROUGHPUT_WARNING_RATIO) {
    return 'warning'
  }
  return 'ok'
}

// A service not receiving its committed clean throughput is an SLA breach.
export function committedSeverity(honored: boolean | null): Severity {
  if (honored === null) {
    return 'ok'
  }
  return honored ? 'ok' : 'warning'
}

// Bloom false-positive counters grow with load; a high cumulative count warns
// that a filter is over-full. Heuristic display threshold only.
export const BLOOM_FP_WARNING = 1_000

export function bloomFpSeverity(falsePositives: number): Severity {
  return falsePositives >= BLOOM_FP_WARNING ? 'warning' : 'ok'
}
