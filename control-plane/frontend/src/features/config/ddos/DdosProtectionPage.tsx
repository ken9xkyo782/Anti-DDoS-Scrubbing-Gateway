import { Link } from 'react-router-dom'
import { Card, PageHeader } from '../../../ui'
import { ProtectionCoverage } from './ProtectionCoverage'

/**
 * Admin-facing DDoS Protection overview. Read-only: it describes every attack
 * class the data plane filters. The one tunable vector — the UDP reflection
 * source-port list — lives on its own AmplificationPage tab, linked below.
 */
export function DdosProtectionPage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="DDoS Protection"
        description="The scrubbing node classifies every packet at line rate and enforces a layered, default-deny policy across L3/L4. Below is the full coverage the data plane filters today."
      />

      <ProtectionCoverage />

      <Card style={{ padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
        <h3 style={{ margin: 0, fontSize: 'var(--font-size-md)', fontWeight: 600 }}>
          Tune UDP reflection &amp; amplification
        </h3>
        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
          Amplification is the one class above with an operator-tunable source-port list. Built-in
          reflectors are always on; you can block additional UDP source ports node-wide.
        </p>
        <Link to="/admin/amplification" style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>
          Manage blocked source ports →
        </Link>
      </Card>
    </div>
  )
}
