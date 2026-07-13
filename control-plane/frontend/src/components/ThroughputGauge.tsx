interface ThroughputGaugeProps {
  cleanBps: number
  capacityBps: number
}

export function ThroughputGauge({ cleanBps, capacityBps }: ThroughputGaugeProps) {
  const percentage = capacityBps > 0 ? Math.min((cleanBps / capacityBps) * 100, 100) : 0

  return (
    <section aria-label="Clean throughput">
      <h3>Clean throughput</h3>
      <progress max="100" value={percentage} />
      <p>{percentage.toFixed(1)}% of capacity</p>
      <p>{cleanBps.toLocaleString()} bps of {capacityBps.toLocaleString()} bps</p>
    </section>
  )
}
