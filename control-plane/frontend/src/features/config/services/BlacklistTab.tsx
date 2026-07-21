import React, { useState } from 'react'
import {
  DataTable,
  Button,
  Dialog,
  ConfirmDialog,
  Badge,
  EmptyState,
  Field,
  Input,
} from '../../../ui'
import {
  useBlacklist,
  useAddBlacklist,
  useRemoveBlacklist,
} from '../../../hooks/resources/useLists'
import { isValidCidrOrIp } from './ServiceForm'
import { toast } from '../../../ui/Toast/Toast'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import type { BlacklistEntryResponse } from '../../../api/types'

interface BlacklistTabProps {
  serviceId: string
}

interface BlacklistFormProps {
  onSubmit: (data: { source_cidr: string }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function BlacklistForm({ onSubmit, onCancel, isSubmitting = false }: BlacklistFormProps) {
  const [sourceCidr, setSourceCidr] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const cidrVal = sourceCidr.trim()
    if (!cidrVal) {
      setErrors({ source_cidr: 'CIDR or IP address is required' })
      return
    }

    if (!isValidCidrOrIp(cidrVal)) {
      setErrors({ source_cidr: 'Must be a valid IP address or CIDR block' })
      return
    }

    try {
      await onSubmit({ source_cidr: cidrVal })
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422 && err.detail) {
          const apiFieldErrors = fieldErrorsFrom422(err.detail)
          setErrors(apiFieldErrors)
        } else {
          setSubmitError(err.message)
        }
      } else if (err instanceof Error) {
        setSubmitError(err.message)
      } else {
        setSubmitError('An unexpected error occurred')
      }
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div style={{ color: 'var(--color-danger, #b42318)', padding: 'var(--space-2)', border: '1px solid', borderRadius: 'var(--radius-md)' }} role="alert">
          {submitError}
        </div>
      )}

      <Field label="Source CIDR or IP" error={errors.source_cidr} required>
        <Input
          value={sourceCidr}
          onChange={(e) => setSourceCidr(e.target.value)}
          placeholder="e.g. 192.168.2.0/24 or 172.16.0.1"
          autoFocus
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={isSubmitting}>
          {isSubmitting ? 'Adding...' : 'Add to Blacklist'}
        </Button>
      </div>
    </form>
  )
}

export function BlacklistTab({ serviceId }: BlacklistTabProps) {
  const { data: blacklist = [], isLoading, error } = useBlacklist(serviceId)
  const [isAddOpen, setIsAddOpen] = useState(false)
  const [deletingEntry, setDeletingEntry] = useState<BlacklistEntryResponse | null>(null)

  const addMutation = useAddBlacklist(serviceId)
  const removeMutation = useRemoveBlacklist(serviceId)

  const handleAddSubmit = async (payload: { source_cidr: string }) => {
    await addMutation.mutateAsync(payload)
    toast({ title: 'IP successfully blacklisted', variant: 'success' })
    setIsAddOpen(false)
  }

  const handleRemoveConfirm = async () => {
    if (!deletingEntry) return
    try {
      await removeMutation.mutateAsync(deletingEntry.source_cidr)
      toast({ title: 'IP removed from blacklist', variant: 'success' })
      setDeletingEntry(null)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to remove from blacklist', description: message, variant: 'error' })
    }
  }

  const columns = [
    {
      key: 'source_cidr',
      header: 'Source CIDR / IP Address',
      render: (entry: BlacklistEntryResponse) => (
        <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{entry.source_cidr}</span>
      ),
    },
    {
      key: 'scope',
      header: 'Scope',
      render: (entry: BlacklistEntryResponse) => (
        <Badge variant={entry.scope === 'global' ? 'danger' : 'default'}>
          {entry.scope.toUpperCase()}
        </Badge>
      ),
    },
    {
      key: 'source',
      header: 'Source',
      render: (entry: BlacklistEntryResponse) => (
        <Badge variant={entry.source === 'feed' ? 'warning' : 'default'}>
          {entry.source.toUpperCase()}
        </Badge>
      ),
    },
    {
      key: 'created_at',
      header: 'Date Created',
      render: (entry: BlacklistEntryResponse) => (
        <span>{new Date(entry.created_at).toLocaleString()}</span>
      ),
    },
    {
      key: 'created_by',
      header: 'Created By',
      render: (entry: BlacklistEntryResponse) => (
        <span>{entry.created_by || 'System'}</span>
      ),
    },
  ]

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
        <h3 style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>Blacklist Semantics</h3>
        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', lineHeight: 'var(--line-height-base)' }}>
          Blacklisted source CIDRs will have their traffic immediately dropped at the scrubbing gateway nodes.
          Service-scoped blacklists apply block rules to this service specifically.
        </p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button variant="primary" onClick={() => setIsAddOpen(true)}>
          Add Blacklist Entry
        </Button>
      </div>

      <DataTable<BlacklistEntryResponse>
        columns={columns}
        data={blacklist}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No blacklist entries configured"
            description="Add malicious or unwanted CIDRs or IP addresses to drop their traffic."
            action={
              <Button variant="primary" onClick={() => setIsAddOpen(true)}>
                Add Blacklist Entry
              </Button>
            }
          />
        }
        rowActions={(entry) => (
          <Button variant="danger" size="sm" onClick={() => setDeletingEntry(entry)}>
            Remove
          </Button>
        )}
      />

      <Dialog
        open={isAddOpen}
        onOpenChange={setIsAddOpen}
        title="Add Blacklist Entry"
      >
        <BlacklistForm
          onSubmit={handleAddSubmit}
          onCancel={() => setIsAddOpen(false)}
          isSubmitting={addMutation.isPending}
        />
      </Dialog>

      <ConfirmDialog
        open={deletingEntry !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingEntry(null)
        }}
        title="Remove Blacklist Entry"
        description={`Are you sure you want to remove ${deletingEntry?.source_cidr} from the blacklist? Traffic from this IP/CIDR will no longer be dropped by default.`}
        confirmLabel="Remove"
        tone="danger"
        onConfirm={handleRemoveConfirm}
      />
    </div>
  )
}
