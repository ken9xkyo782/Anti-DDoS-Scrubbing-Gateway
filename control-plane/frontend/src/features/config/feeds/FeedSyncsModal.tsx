import { Dialog, DataTable, Badge, Spinner } from '../../../ui'
import { useFeedSyncs } from '../../../hooks/resources/useFeeds'
import type { FeedSyncRunResponse } from '../../../api/types'

interface FeedSyncsModalProps {
  feedId: string | null
  feedName: string
  isOpen: boolean
  onClose: (open: boolean) => void
}

export function FeedSyncsModal({ feedId, feedName, isOpen, onClose }: FeedSyncsModalProps) {
  const { data: syncs = [], isLoading, error } = useFeedSyncs(feedId)

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'success':
        return <Badge variant="success">Success</Badge>
      case 'failed':
        return <Badge variant="danger">Failed</Badge>
      case 'partial':
        return <Badge variant="warning">Partial</Badge>
      case 'running':
        return <Badge variant="info">Running</Badge>
      case 'queued':
      default:
        return <Badge>Queued</Badge>
    }
  }

  const getTriggerText = (trigger: string) => {
    switch (trigger) {
      case 'feed_manual':
        return 'Manual'
      case 'feed_schedule':
        return 'Schedule'
      case 'feed_delete':
        return 'Delete'
      case 'feed_dry_run':
        return 'Dry Run Sync'
      default:
        return trigger
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={onClose} title={`Sync History: ${feedName}`}>
      <div style={{ minWidth: '600px', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        {isLoading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-8)' }}>
            <Spinner size="lg" />
          </div>
        ) : (
          <DataTable<FeedSyncRunResponse>
            columns={[
              {
                key: 'sequence',
                header: 'Run #',
                render: (run) => (
                  <span style={{ fontWeight: 600 }}>
                    #{run.sequence} {run.dry_run && <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>(Dry Run)</span>}
                  </span>
                ),
              },
              {
                key: 'status',
                header: 'Status',
                render: (run) => (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
                    {getStatusBadge(run.status)}
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      {getTriggerText(run.trigger)}
                    </span>
                  </div>
                ),
              },
              {
                key: 'changes',
                header: 'Changes',
                render: (run) => (
                  <div style={{ fontSize: '13px' }}>
                    <div style={{ color: 'var(--color-success-text, #027a48)' }}>+{run.added} added</div>
                    <div style={{ color: 'var(--color-danger, #b42318)' }}>-{run.removed} removed</div>
                  </div>
                ),
              },
              {
                key: 'stats',
                header: 'Stats',
                render: (run) => (
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    <div>Valid: {run.valid}</div>
                    <div>Dup: {run.duplicates}</div>
                    {run.overlap_count > 0 && (
                      <div style={{ color: 'var(--color-warning-text, #b54708)', fontWeight: 500 }}>
                        Overlap: {run.overlap_count}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: 'time',
                header: 'Duration / Finished',
                render: (run) => (
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    <div>{run.duration_ms ? `${(run.duration_ms / 1000).toFixed(2)}s` : '-'}</div>
                    <div>{run.finished_at ? new Date(run.finished_at).toLocaleString() : 'In Progress'}</div>
                  </div>
                ),
              },
              {
                key: 'error',
                header: 'Details',
                render: (run) =>
                  run.error ? (
                    <span
                      style={{
                        fontSize: '11px',
                        color: 'var(--color-danger, #b42318)',
                        wordBreak: 'break-word',
                        maxWidth: '200px',
                        display: 'block',
                      }}
                      title={run.error}
                    >
                      {run.error}
                    </span>
                  ) : (
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>-</span>
                  ),
              },
            ]}
            data={syncs}
            isLoading={isLoading}
            error={error?.message}
            emptyState={
              <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-muted)' }}>
                No sync runs recorded for this feed yet.
              </div>
            }
          />
        )}
      </div>
    </Dialog>
  )
}
