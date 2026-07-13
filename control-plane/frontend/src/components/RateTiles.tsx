interface RateTilesProps {
  pps: number
  bps: number
}

function formatRate(value: number, unit: string) {
  return `${value.toLocaleString()} ${unit}`
}

export function RateTiles({ pps, bps }: RateTilesProps) {
  return (
    <dl>
      <div>
        <dt>Packets per second</dt>
        <dd>{formatRate(pps, 'pps')}</dd>
      </div>
      <div>
        <dt>Bits per second</dt>
        <dd>{formatRate(bps, 'bps')}</dd>
      </div>
    </dl>
  )
}
