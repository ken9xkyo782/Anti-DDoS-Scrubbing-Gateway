import type { NodeHealth } from '../hooks/useNodeTelemetry'

interface XdpModeFlagProps {
  mode: NodeHealth['xdp_mode']
}

export function XdpModeFlag({ mode }: XdpModeFlagProps) {
  if (mode === 'generic' || mode === 'offline') {
    return <p role="alert" style={{ color: '#b42318', fontWeight: 700 }}>Critical: XDP mode {mode}</p>
  }

  return <p>XDP mode: {mode}</p>
}
