import { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  ConfirmDialog,
  Badge,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import {
  useAmplificationConfig,
  useAddBlockedPort,
  useRemoveBlockedPort,
} from '../../../hooks/resources/useAmplificationConfig'
import { BlockedPortForm } from './BlockedPortForm'
import { ProtectionCoverage } from './ProtectionCoverage'
import type { BlockedPortResponse } from '../../../api/types'

/**
 * Human-readable reflector behind each compile-time amplification source port.
 * Labels only — the authoritative set is the data-plane `amp_port_hardcoded`
 * switch (blacklist.h), mirrored server-side by HARDCODED_AMP_PORTS and
 * delivered as `hardcoded_ports`. A port we have no label for still renders.
 */
const REFLECTOR_LABELS: Record<number, string> = {
  17: 'QOTD',
  19: 'CHARGEN',
  53: 'DNS',
  111: 'Portmap / RPC',
  123: 'NTP',
  137: 'NetBIOS-NS',
  161: 'SNMP',
  389: 'CLDAP',
  520: 'RIP',
  1900: 'SSDP',
  5353: 'mDNS',
  11211: 'memcached',
}

/**
 * One row of the unified blocked-source-port table. Built-in rows come from the
 * compiled data-plane set and are read-only; dynamic rows are the admin-managed
 * entries the reconcile lane pushes into `udp_blocked_port_bitmap`.
 */
type BlockedPortRow =
  | { kind: 'builtin'; port: number }
  | { kind: 'dynamic'; port: number; entry: BlockedPortResponse }

export function DdosProtectionPage() {
  const { data: config, isLoading, error } = useAmplificationConfig()
  const [isAddOpen, setIsAddOpen] = useState(false)
  const [removingEntry, setRemovingEntry] = useState<BlockedPortResponse | null>(null)

  const addMutation = useAddBlockedPort()
  const removeMutation = useRemoveBlockedPort()

  const hardcodedPorts = config?.hardcoded_ports ?? []
  const dynamicPorts = config?.dynamic_ports ?? []

  // Built-ins first (they are always on), then the tunable admin entries.
  const rows: BlockedPortRow[] = [
    ...[...hardcodedPorts].sort((a, b) => a - b).map((port) => ({ kind: 'builtin' as const, port })),
    ...dynamicPorts.map((entry) => ({ kind: 'dynamic' as const, port: entry.port, entry })),
  ]

  const handleAddSubmit = async (payload: { port: number; note?: string | null }) => {
    await addMutation.mutateAsync(payload)
    toast({ title: 'Blocked-port list updated; applying to data-plane', variant: 'success' })
    setIsAddOpen(false)
  }

  const handleRemove = async () => {
    if (!removingEntry) return
    try {
      await removeMutation.mutateAsync(removingEntry.port)
      toast({ title: 'Blocked-port list updated; applying to data-plane', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to remove blocked port', description: message, variant: 'error' })
    } finally {
      setRemovingEntry(null)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="DDoS Protection"
        description="The scrubbing node classifies every packet at line rate and enforces a layered, default-deny policy across L3/L4. Below is the full coverage the data plane filters today; the UDP amplification source ports are the one vector you tune from here."
        actions={
          <Button variant="primary" onClick={() => setIsAddOpen(true)}>
            Add Blocked Port
          </Button>
        }
      />

      <ProtectionCoverage />

      {/* Unified blocked-source-port section: built-ins (locked) + dynamic entries */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 'var(--font-size-lg)', fontWeight: 600 }}>
            UDP reflection &amp; amplification
          </h2>
          <p style={{ margin: 'var(--space-1) 0 0 0', fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
            Dynamic blocked source ports — every UDP source port the node drops today. Built-in
            reflectors are compiled into the data plane and always on; the entries below them are
            yours to tune.
          </p>
        </div>

        <DataTable<BlockedPortRow>
          columns={[
            {
              key: 'port',
              header: 'Source Port',
              render: (row) => (
                <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>UDP/{row.port}</span>
              ),
            },
            {
              key: 'note',
              header: 'Note / Reason',
              render: (row) =>
                row.kind === 'builtin' ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <Badge variant="success">Built-in</Badge>
                    <span style={{ color: 'var(--text-muted)' }}>
                      {REFLECTOR_LABELS[row.port] ?? 'Well-known reflector'}
                    </span>
                  </span>
                ) : (
                  <span>{row.entry.note || '—'}</span>
                ),
            },
            {
              key: 'created_at',
              header: 'Blocked At',
              render: (row) =>
                row.kind === 'builtin' ? (
                  <span style={{ color: 'var(--text-muted)' }}>Always on</span>
                ) : (
                  <span>{new Date(row.entry.created_at).toLocaleString()}</span>
                ),
            },
          ]}
          data={rows}
          isLoading={isLoading}
          error={error?.message}
          rowActions={(row) =>
            row.kind === 'dynamic' ? (
              <Button
                variant="danger"
                size="sm"
                onClick={() => setRemovingEntry(row.entry)}
                title="Remove blocked port"
              >
                Remove
              </Button>
            ) : null
          }
        />

        {!isLoading && !error && dynamicPorts.length === 0 && (
          <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
            No custom source ports blocked yet — only the built-in reflectors above are enforced. Use{' '}
            <strong>Add Blocked Port</strong> to block a new amplification vector node-wide.
          </p>
        )}
      </section>

      {/* Add Dialog */}
      <Dialog open={isAddOpen} onOpenChange={setIsAddOpen} title="Add Blocked UDP Port">
        <BlockedPortForm
          onSubmit={handleAddSubmit}
          onCancel={() => setIsAddOpen(false)}
          isSubmitting={addMutation.isPending}
        />
      </Dialog>

      {/* Remove Confirmation */}
      <ConfirmDialog
        open={removingEntry !== null}
        onOpenChange={(open) => {
          if (!open) setRemovingEntry(null)
        }}
        title="Remove Blocked Port"
        description={`Are you sure you want to unblock UDP port ${removingEntry?.port}? This port will no longer be dropped by the dynamic amplification filter.`}
        confirmLabel="Remove"
        tone="danger"
        onConfirm={handleRemove}
      />
    </div>
  )
}
