import { PageHeader } from '../../../ui'
import { ProtectionCoverage } from './ProtectionCoverage'

/**
 * Tenant-facing, read-only DDoS Protection view. Shows only the protection
 * coverage summary — no blocked-port management, so it makes no admin-only API
 * calls. Port administration lives on the admin DdosProtectionPage.
 */
export function DdosCoveragePage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="DDoS Protection"
        description="Every packet destined for your services is classified at line rate and filtered by a layered, default-deny policy. This is the protection the scrubbing node applies on your behalf — no setup required."
      />
      <ProtectionCoverage />
    </div>
  )
}
