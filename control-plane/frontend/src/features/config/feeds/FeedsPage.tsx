import { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  EmptyState,
  ConfirmDialog,
  Badge,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import {
  useFeeds,
  useCreateFeed,
  useUpdateFeed,
  useDeleteFeed,
  useSyncFeed,
} from '../../../hooks/resources/useFeeds'
import { FeedForm, type FeedFormPayload } from './FeedForm'
import { FeedSyncsModal } from './FeedSyncsModal'
import type { FeedSourceResponse } from '../../../api/types'

export function FeedsPage() {
  const { data: feeds = [], isLoading, error } = useFeeds()
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [editingFeed, setEditingFeed] = useState<FeedSourceResponse | null>(null)
  const [deletingFeed, setDeletingFeed] = useState<FeedSourceResponse | null>(null)
  const [historyFeed, setHistoryFeed] = useState<FeedSourceResponse | null>(null)

  const createMutation = useCreateFeed()
  const updateMutation = useUpdateFeed(editingFeed?.id ?? '')
  const deleteMutation = useDeleteFeed(deletingFeed?.id ?? '')

  const handleCreateSubmit = async (payload: FeedFormPayload) => {
    await createMutation.mutateAsync(payload)
    toast({ title: 'Threat feed created successfully', variant: 'success' })
    setIsCreateOpen(false)
  }

  const handleEditSubmit = async (payload: FeedFormPayload) => {
    if (!editingFeed) return
    await updateMutation.mutateAsync(payload)
    toast({ title: 'Threat feed updated successfully', variant: 'success' })
    setEditingFeed(null)
  }

  const handleDelete = async () => {
    if (!deletingFeed) return
    try {
      await deleteMutation.mutateAsync()
      toast({ title: 'Threat feed deleted successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to delete threat feed', description: message, variant: 'error' })
    } finally {
      setDeletingFeed(null)
    }
  }

  const formatInterval = (seconds: number) => {
    if (seconds < 60) return `${seconds}s`
    const mins = Math.floor(seconds / 60)
    if (mins < 60) return `${mins} mins`
    const hours = Math.floor(mins / 60)
    const remMins = mins % 60
    if (hours < 24) return `${hours} hrs${remMins > 0 ? ` ${remMins}m` : ''}`
    const days = Math.floor(hours / 24)
    const remHours = hours % 24
    return `${days} days${remHours > 0 ? ` ${remHours}h` : ''}`
  }

  const getSyncStatusBadge = (status: string | null) => {
    if (!status) return <Badge>Never Synced</Badge>
    switch (status) {
      case 'success':
        return <Badge variant="success">Success</Badge>
      case 'failed':
        return <Badge variant="danger">Failed</Badge>
      case 'partial':
        return <Badge variant="warning">Partial Sync</Badge>
      case 'running':
        return <Badge variant="info">Syncing</Badge>
      case 'queued':
      default:
        return <Badge>Queued</Badge>
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Threat Feeds Management"
        description="Configure threat intelligence feed sources and automatic schedule syncs to import network blocklists."
        actions={
          feeds.length > 0 && (
            <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
              Add Feed Source
            </Button>
          )
        }
      />

      <DataTable<FeedSourceResponse>
        columns={[
          {
            key: 'name',
            header: 'Feed Name',
            render: (feed) => (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
                <span style={{ fontWeight: 600 }}>{feed.name}</span>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)', wordBreak: 'break-all', maxWidth: '300px' }}>
                  {feed.url}
                </span>
              </div>
            ),
          },
          {
            key: 'interval',
            header: 'Sync Interval',
            render: (feed) => (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
                <span>{formatInterval(feed.sync_interval_seconds)}</span>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  {feed.enabled ? 'Schedule Active' : 'Schedule Inactive'}
                </span>
              </div>
            ),
          },
          {
            key: 'status',
            header: 'Last Sync Status',
            render: (feed) => (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)', alignItems: 'flex-start' }}>
                {getSyncStatusBadge(feed.last_status)}
                {feed.last_error && (
                  <span
                    style={{ fontSize: '11px', color: 'var(--color-danger, #b42318)', maxWidth: '200px', wordBreak: 'break-all' }}
                    title={feed.last_error}
                  >
                    {feed.last_error}
                  </span>
                )}
              </div>
            ),
          },
          {
            key: 'sync_times',
            header: 'Sync Timing',
            render: (feed) => (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                <div>Last: {feed.last_sync_at ? new Date(feed.last_sync_at).toLocaleString() : 'Never'}</div>
                <div>Next: {feed.next_sync_at ? new Date(feed.next_sync_at).toLocaleString() : 'N/A'}</div>
              </div>
            ),
          },
        ]}
        data={feeds}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No threat feeds found"
            description="Configure your first remote threat feed source to pull network intelligence automatically."
            action={
              <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
                Add Feed Source
              </Button>
            }
          />
        }
        rowActions={(feed) => (
          <FeedRowActions
            feed={feed}
            onEdit={setEditingFeed}
            onDelete={setDeletingFeed}
            onViewHistory={setHistoryFeed}
          />
        )}
      />

      {/* Create Dialog */}
      <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen} title="Add Threat Feed Source">
        <FeedForm
          onSubmit={handleCreateSubmit}
          onCancel={() => setIsCreateOpen(false)}
          isSubmitting={createMutation.isPending}
        />
      </Dialog>

      {/* Edit Dialog */}
      <Dialog
        open={editingFeed !== null}
        onOpenChange={(open) => {
          if (!open) setEditingFeed(null)
        }}
        title="Edit Threat Feed Source"
      >
        {editingFeed && (
          <FeedForm
            feed={editingFeed}
            onSubmit={handleEditSubmit}
            onCancel={() => setEditingFeed(null)}
            isSubmitting={updateMutation.isPending}
          />
        )}
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={deletingFeed !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingFeed(null)
        }}
        title="Delete Threat Feed Source"
        description={`Are you sure you want to delete threat feed source "${deletingFeed?.name}"? The blocklist entries imported by this feed will no longer receive schedule updates. This action cannot be undone.`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDelete}
      />

      {/* Sync History Modal */}
      {historyFeed && (
        <FeedSyncsModal
          feedId={historyFeed.id}
          feedName={historyFeed.name}
          isOpen={historyFeed !== null}
          onClose={() => setHistoryFeed(null)}
        />
      )}
    </div>
  )
}

interface FeedRowActionsProps {
  feed: FeedSourceResponse
  onEdit: (feed: FeedSourceResponse) => void
  onDelete: (feed: FeedSourceResponse) => void
  onViewHistory: (feed: FeedSourceResponse) => void
}

function FeedRowActions({ feed, onEdit, onDelete, onViewHistory }: FeedRowActionsProps) {
  const syncMutation = useSyncFeed(feed.id)

  const handleSyncNow = async () => {
    try {
      await syncMutation.mutateAsync()
      toast({ title: 'Manual sync triggered successfully', description: `Sync job for feed "${feed.name}" has been enqueued.`, variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to trigger sync', description: message, variant: 'error' })
    }
  }

  const isSyncing = syncMutation.isPending || feed.last_status === 'running'

  return (
    <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
      <Button variant="secondary" size="sm" onClick={handleSyncNow} loading={syncMutation.isPending} disabled={isSyncing}>
        Sync Now
      </Button>
      <Button variant="secondary" size="sm" onClick={() => onViewHistory(feed)}>
        History
      </Button>
      <Button variant="secondary" size="sm" onClick={() => onEdit(feed)} disabled={isSyncing}>
        Edit
      </Button>
      <Button variant="danger" size="sm" onClick={() => onDelete(feed)} disabled={isSyncing}>
        Delete
      </Button>
    </div>
  )
}
