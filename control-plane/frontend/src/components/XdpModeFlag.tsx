import type { NodeHealth } from '../hooks/useNodeTelemetry'
import styles from './dashboard.module.css'

interface XdpModeFlagProps {
  mode: NodeHealth['xdp_mode']
}

export function XdpModeFlag({ mode }: XdpModeFlagProps) {
  if (mode === 'generic' || mode === 'offline') {
    return (
      <p role="alert" className={`${styles.badge} ${styles.badgeCrit}`}>
        <span className={styles.dot} aria-hidden="true" />
        Critical: XDP mode {mode}
      </p>
    )
  }

  const tone = mode === 'native' ? styles.badgeOk : styles.badgeNeutral

  return (
    <p className={`${styles.badge} ${tone}`}>
      <span className={styles.dot} aria-hidden="true" />
      XDP mode: {mode}
    </p>
  )
}
