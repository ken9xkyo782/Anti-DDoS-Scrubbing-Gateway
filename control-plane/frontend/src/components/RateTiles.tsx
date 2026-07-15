import styles from './dashboard.module.css'

interface RateTilesProps {
  pps: number
  bps: number
}

function formatRate(value: number, unit: string) {
  return `${value.toLocaleString()} ${unit}`
}

export function RateTiles({ pps, bps }: RateTilesProps) {
  return (
    <dl className={styles.tiles}>
      <div className={styles.tile}>
        <dt className={styles.tileLabel}>Packets per second</dt>
        <dd className={styles.tileValue}>{formatRate(pps, 'pps')}</dd>
      </div>
      <div className={styles.tile}>
        <dt className={styles.tileLabel}>Bits per second</dt>
        <dd className={styles.tileValue}>{formatRate(bps, 'bps')}</dd>
      </div>
    </dl>
  )
}
