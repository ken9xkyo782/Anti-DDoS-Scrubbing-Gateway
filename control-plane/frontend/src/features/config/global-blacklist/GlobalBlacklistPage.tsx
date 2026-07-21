import { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  ConfirmDialog,
  Badge,
  EmptyState,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import {
  useGlobalBlacklist,
  useAddGlobalBlacklist,
  useRemoveGlobalBlacklist,
} from '../../../hooks/resources/useGlobalBlacklist'
import { GlobalBlacklistForm } from './GlobalBlacklistForm'
import type { BlacklistEntryResponse } from '../../../api/types'

export function GlobalBlacklistPage() {
  const { data: blacklist = [], isLoading, error } = useGlobalBlacklist()
  const [isAddOpen, setIsAddOpen] = useState(false)
  const [removingEntry, setRemovingEntry] = useState<BlacklistEntryResponse | null>(null)

  const addMutation = useAddGlobalBlacklist()
  const removeMutation = useRemoveGlobalBlacklist()

  const handleAddSubmit = async (payload: { source_cidr: string }) => {
    await addMutation.mutateAsync(payload)
    toast({ title: 'IP/CIDR added to global blacklist', variant: 'success' })
    setIsAddOpen(false)
  }

  const handleRemove = async () => {
    if (!removingEntry) return
    try {
      await removeMutation.mutateAsync(removingEntry.source_cidr)
      toast({ title: 'IP/CIDR removed from global blacklist', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to remove from global blacklist', description: message, variant: 'error' })
    } finally {
      setRemovingEntry(null)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Global Blacklist"
        description="Manage the node-wide blocklist. Traffic matching these IP addresses or CIDR blocks will be blocked immediately across all services."
        actions={
          blacklist.length > 0 && (
            <Button variant="primary" onClick={() => setIsAddOpen(true)}>
              Add Blacklist Entry
            </Button>
          )
        }
      />

      <DataTable<BlacklistEntryResponse>
        columns={[
          {
            key: 'source_cidr',
            header: 'Blocked IP / CIDR',
            render: (entry) => <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{entry.source_cidr}</span>,
          },
          {
            key: 'source',
            header: 'Intel Source',
            render: (entry) => (
              <Badge variant={entry.source === 'feed' ? 'warning' : 'default'}>
                {entry.source === 'feed' ? 'Threat Feed' : 'Manual Admin'}
              </Badge>
            ),
          },
          {
            key: 'created_at',
            header: 'Blocked At',
            render: (entry) => <span>{new Date(entry.created_at).toLocaleString()}</span>,
          },
        ]}
        data={blacklist}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="Global blacklist is empty"
            description="Add IP addresses or subnet CIDR blocks to restrict node access globally."
            action={
              <Button variant="primary" onClick={() => setIsAddOpen(true)}>
                Add Blacklist Entry
              </Button>
            }
          />
        }
        rowActions={(entry) => (
          <Button
            variant="danger"
            size="sm"
            onClick={() => setRemovingEntry(entry)}
            disabled={entry.source === 'feed'}
            title={entry.source === 'feed' ? 'Feed-sourced entries must be modified via the Threat Feed source configuration' : 'Remove entry'}
          >
            Remove
          </Button>
        )}
      />

      {/* Add Dialog */}
      <Dialog open={isAddOpen} onOpenChange={setIsAddOpen} title="Add Global Blacklist Entry">
        <GlobalBlacklistForm
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
        title="Remove Blacklist Entry"
        description={`Are you sure you want to remove "${removingEntry?.source_cidr}" from the global blacklist? This IP/CIDR will no longer be blocked node-wide.`}
        confirmLabel="Remove"
        tone="danger"
        onConfirm={handleRemove}
      />
    </div>
  )
}
