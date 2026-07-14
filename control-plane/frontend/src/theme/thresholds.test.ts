import { describe, expect, it } from 'vitest'

import {
  bloomFpSeverity,
  committedSeverity,
  mapErrorSeverity,
  severityColor,
  throughputSeverity,
} from './thresholds'

describe('threshold severities', () => {
  it('flags any map error as critical', () => {
    expect(mapErrorSeverity(0)).toBe('ok')
    expect(mapErrorSeverity(1)).toBe('critical')
  })

  it('warns as clean throughput nears capacity and is critical at capacity', () => {
    expect(throughputSeverity(20_000_000_000, 40_000_000_000)).toBe('ok')
    expect(throughputSeverity(38_000_000_000, 40_000_000_000)).toBe('warning')
    expect(throughputSeverity(40_000_000_000, 40_000_000_000)).toBe('critical')
    expect(throughputSeverity(10, 0)).toBe('ok')
  })

  it('warns when a committed plan is breached', () => {
    expect(committedSeverity(true)).toBe('ok')
    expect(committedSeverity(false)).toBe('warning')
    expect(committedSeverity(null)).toBe('ok')
  })

  it('warns on a high cumulative bloom false-positive count', () => {
    expect(bloomFpSeverity(0)).toBe('ok')
    expect(bloomFpSeverity(2_000)).toBe('warning')
  })

  it('maps each severity to a distinct color', () => {
    expect(severityColor('ok')).not.toBe(severityColor('warning'))
    expect(severityColor('warning')).not.toBe(severityColor('critical'))
    expect(severityColor('critical')).toBe('#b42318')
  })
})
