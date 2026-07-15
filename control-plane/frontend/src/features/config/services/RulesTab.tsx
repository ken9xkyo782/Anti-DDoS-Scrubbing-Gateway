import { useState } from 'react'
import {
  DataTable,
  Button,
  Dialog,
  ConfirmDialog,
  Badge,
  EmptyState,
} from '../../../ui'
import {
  useRules,
  useCreateRule,
  useUpdateRule,
  useDeleteRule,
} from '../../../hooks/resources/useRules'
import { RuleForm } from './RuleForm'
import { toast } from '../../../ui/Toast/Toast'
import type { RuleResponse, Protocol } from '../../../api/types'

interface RulesTabProps {
  serviceId: string
}

function formatPorts(lo: number | null, hi: number | null): string {
  if (lo == null && hi == null) return 'Any'
  if (lo === hi) return String(lo)
  return `${lo}-${hi}`
}

function formatLimits(pps: number | null, bps: number | null): string {
  if (pps == null && bps == null) return '-'
  const parts: string[] = []
  if (pps != null) parts.push(`${pps.toLocaleString()} pps`)
  if (bps != null) {
    if (bps >= 1_000_000) {
      parts.push(`${(bps / 1_000_000).toFixed(1)} Mbps`)
    } else if (bps >= 1_000) {
      parts.push(`${(bps / 1_000).toFixed(1)} Kbps`)
    } else {
      parts.push(`${bps} bps`)
    }
  }
  return parts.join(', ')
}

export function RulesTab({ serviceId }: RulesTabProps) {
  const { data: rules = [], isLoading, error } = useRules(serviceId)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<RuleResponse | null>(null)
  const [deletingRule, setDeletingRule] = useState<RuleResponse | null>(null)

  const createMutation = useCreateRule(serviceId)
  const updateMutation = useUpdateRule(serviceId, editingRule?.id ?? '')
  const deleteMutation = useDeleteRule(serviceId, deletingRule?.id ?? '')

  // Evaluation order is defined by ascending priority
  const sortedRules = [...rules].sort((a, b) => a.priority - b.priority)

  const handleCreateSubmit = async (payload: {
    priority: number
    protocol: Protocol
    src_port_lo?: number | null
    src_port_hi?: number | null
    dst_port_lo?: number | null
    dst_port_hi?: number | null
    pps?: number | null
    bps?: number | null
    enabled: boolean
  }) => {
    try {
      await createMutation.mutateAsync(payload)
      toast({ title: 'Allow rule created successfully', variant: 'success' })
      setIsCreateOpen(false)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to create allow rule', description: message, variant: 'error' })
      throw err // Let form handle inline validation mapping
    }
  }

  const handleEditSubmit = async (payload: {
    priority?: number
    protocol?: Protocol
    src_port_lo?: number | null
    src_port_hi?: number | null
    dst_port_lo?: number | null
    dst_port_hi?: number | null
    pps?: number | null
    bps?: number | null
    enabled?: boolean
  }) => {
    try {
      await updateMutation.mutateAsync(payload)
      toast({ title: 'Allow rule updated successfully', variant: 'success' })
      setEditingRule(null)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to update allow rule', description: message, variant: 'error' })
      throw err // Let form handle inline validation mapping
    }
  }

  const handleDeleteConfirm = async () => {
    if (!deletingRule) return
    try {
      await deleteMutation.mutateAsync()
      toast({ title: 'Allow rule deleted successfully', variant: 'success' })
      setDeletingRule(null)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to delete allow rule', description: message, variant: 'error' })
    }
  }

  const columns = [
    {
      key: 'order',
      header: 'Evaluation Order',
      render: (_: RuleResponse) => {
        const index = sortedRules.findIndex((r) => r.id === _.id)
        return (
          <span style={{ fontWeight: 600, color: 'var(--text-muted)' }}>
            #{index + 1}
          </span>
        )
      },
    },
    {
      key: 'priority',
      header: 'Priority',
      render: (rule: RuleResponse) => (
        <span style={{ fontFamily: 'monospace', fontWeight: 500 }}>{rule.priority}</span>
      ),
    },
    {
      key: 'protocol',
      header: 'Protocol',
      render: (rule: RuleResponse) => (
        <span style={{ fontWeight: 500 }}>{rule.protocol.toUpperCase()}</span>
      ),
    },
    {
      key: 'src_ports',
      header: 'Source Ports',
      render: (rule: RuleResponse) => (
        <span style={{ fontFamily: 'monospace' }}>
          {rule.protocol === 'icmp' ? '-' : formatPorts(rule.src_port_lo, rule.src_port_hi)}
        </span>
      ),
    },
    {
      key: 'dst_ports',
      header: 'Dest Ports',
      render: (rule: RuleResponse) => (
        <span style={{ fontFamily: 'monospace' }}>
          {rule.protocol === 'icmp' ? '-' : formatPorts(rule.dst_port_lo, rule.dst_port_hi)}
        </span>
      ),
    },
    {
      key: 'limits',
      header: 'Rate Limits',
      render: (rule: RuleResponse) => formatLimits(rule.pps, rule.bps),
    },
    {
      key: 'status',
      header: 'Status',
      render: (rule: RuleResponse) => (
        <Badge variant={rule.enabled ? 'success' : 'default'}>
          {rule.enabled ? 'Enabled' : 'Disabled'}
        </Badge>
      ),
    },
    {
      key: 'warnings',
      header: 'Warnings',
      render: (rule: RuleResponse) => (
        rule.warnings && rule.warnings.length > 0 ? (
          <div
            title={rule.warnings.join('\n')}
            style={{
              color: 'var(--color-warning, #d97706)',
              cursor: 'help',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--space-1)',
              fontSize: 'var(--font-size-xs)',
              fontWeight: 500
            }}
          >
            <span>⚠️</span>
            <span>{rule.warnings.length} warning(s)</span>
          </div>
        ) : null
      ),
    },
  ]

  const reachedMaxRules = rules.length >= 16

  const headerActions = (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 'var(--space-1)' }}>
      <Button
        variant="primary"
        onClick={() => setIsCreateOpen(true)}
        disabled={reachedMaxRules}
      >
        Add Allow Rule
      </Button>
      {reachedMaxRules && (
        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-warning, #9a6700)' }}>
          Max limit of 16 rules reached
        </span>
      )}
    </div>
  )

  const emptyStateAction = (
    <Button variant="primary" onClick={() => setIsCreateOpen(true)} disabled={reachedMaxRules}>
      Add Allow Rule
    </Button>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{
        backgroundColor: 'var(--bg-elevated)',
        padding: 'var(--space-4)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-1)'
      }}>
        <h3 style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>Rule Matching & Semantics</h3>
        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', lineHeight: 'var(--line-height-base)' }}>
          Rules are evaluated in order of ascending priority (evaluation order). The first rule that matches a packet allows it through.
          If no rules match, traffic is blocked by default. Lower priority rules will not be evaluated if a higher priority rule matches first.
        </p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        {rules.length > 0 && headerActions}
      </div>

      <DataTable<RuleResponse>
        columns={columns}
        data={sortedRules}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No allow rules configured"
            description="Create policy rules to explicitly specify which network traffic should be allowed through to your service."
            action={emptyStateAction}
          />
        }
        rowActions={(rule) => (
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <Button variant="secondary" size="sm" onClick={() => setEditingRule(rule)}>
              Edit
            </Button>
            <Button variant="danger" size="sm" onClick={() => setDeletingRule(rule)}>
              Delete
            </Button>
          </div>
        )}
      />

      {/* Create Dialog */}
      <Dialog
        open={isCreateOpen}
        onOpenChange={setIsCreateOpen}
        title="Add Allow Rule"
      >
        <RuleForm
          existingRules={rules}
          onSubmit={handleCreateSubmit}
          onCancel={() => setIsCreateOpen(false)}
          isSubmitting={createMutation.isPending}
          serviceId={serviceId}
        />
      </Dialog>

      {/* Edit Dialog */}
      <Dialog
        open={editingRule !== null}
        onOpenChange={(open) => {
          if (!open) setEditingRule(null)
        }}
        title="Edit Allow Rule"
      >
        {editingRule && (
          <RuleForm
            rule={editingRule}
            existingRules={rules}
            onSubmit={handleEditSubmit}
            onCancel={() => setEditingRule(null)}
            isSubmitting={updateMutation.isPending}
            serviceId={serviceId}
          />
        )}
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={deletingRule !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingRule(null)
        }}
        title="Delete Allow Rule"
        description={`Are you sure you want to delete the rule with priority ${deletingRule?.priority}? This action will take effect immediately on the gateway.`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDeleteConfirm}
      />
    </div>
  )
}
