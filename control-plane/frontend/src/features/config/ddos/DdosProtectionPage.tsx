import { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  ConfirmDialog,
  Badge,
  EmptyState,
  Card,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import {
  useAmplificationConfig,
  useAddBlockedPort,
  useRemoveBlockedPort,
} from '../../../hooks/resources/useAmplificationConfig'
import { BlockedPortForm } from './BlockedPortForm'
import type { BlockedPortResponse } from '../../../api/types'

export function DdosProtectionPage() {
  const { data: config, isLoading, error } = useAmplificationConfig()
  const [isAddOpen, setIsAddOpen] = useState(false)
  const [removingEntry, setRemovingEntry] = useState<BlockedPortResponse | null>(null)

  const addMutation = useAddBlockedPort()
  const removeMutation = useRemoveBlockedPort()

  const hardcodedPorts = config?.hardcoded_ports ?? []
  const dynamicPorts = config?.dynamic_ports ?? []

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
        description="Manage UDP amplification attack vectors and source port drop rules. Hardcoded protocol ports are enforced automatically, and dynamic ports converge to the data plane in background worker ticks."
        actions={
          <Button variant="primary" onClick={() => setIsAddOpen(true)}>
            Add Blocked Port
          </Button>
        }
      />

      {/* Hardcoded Built-in Ports Section */}
      <Card style={{ padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 'var(--font-size-md, 1rem)', fontWeight: 600 }}>
              Built-in blocked source ports (always on)
            </h3>
            <p style={{ margin: 'var(--space-1) 0 0 0', fontSize: 'var(--font-size-sm, 0.875rem)', color: 'var(--color-text-muted, #666)' }}>
              Static data-plane protocol filters compiled directly into eBPF.
            </p>
          </div>
          <Badge variant="default">Authoritative DP Header</Badge>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
          {hardcodedPorts.map((port) => (
            <span
              key={port}
              style={{
                fontFamily: 'monospace',
                fontSize: 'var(--font-size-sm, 0.875rem)',
                padding: 'var(--space-1) var(--space-2)',
                backgroundColor: 'var(--color-bg-subtle, #f3f4f6)',
                border: '1px solid var(--color-border-subtle, #e5e7eb)',
                borderRadius: 'var(--radius-sm, 4px)',
                fontWeight: 600,
              }}
            >
              UDP/{port}
            </span>
          ))}
        </div>
      </Card>

      {/* Dynamic Blocked Ports Section */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        <h3 style={{ margin: 0, fontSize: 'var(--font-size-md, 1rem)', fontWeight: 600 }}>
          Dynamic blocked source ports
        </h3>

        <DataTable<BlockedPortResponse>
          columns={[
            {
              key: 'port',
              header: 'Port Number',
              render: (entry) => (
                <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>UDP/{entry.port}</span>
              ),
            },
            {
              key: 'note',
              header: 'Note / Reason',
              render: (entry) => <span>{entry.note || '—'}</span>,
            },
            {
              key: 'created_at',
              header: 'Blocked At',
              render: (entry) => <span>{new Date(entry.created_at).toLocaleString()}</span>,
            },
          ]}
          data={dynamicPorts}
          isLoading={isLoading}
          error={error?.message}
          emptyState={
            <EmptyState
              title="No dynamic blocked UDP ports"
              description="Add custom UDP source ports to block amplification vectors across the node."
              action={
                <Button variant="primary" onClick={() => setIsAddOpen(true)}>
                  Add Blocked Port
                </Button>
              }
            />
          }
          rowActions={(entry) => (
            <Button
              variant="danger"
              size="sm"
              onClick={() => setRemovingEntry(entry)}
              title="Remove blocked port"
            >
              Remove
            </Button>
          )}
        />
      </div>

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
