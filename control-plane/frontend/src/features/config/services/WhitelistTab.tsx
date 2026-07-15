import React, { useState } from 'react'
import {
  DataTable,
  Button,
  Dialog,
  ConfirmDialog,
  Card,
  CardContent,
  EmptyState,
  Field,
  Input,
} from '../../../ui'
import {
  useWhitelist,
  useAddWhitelist,
  useRemoveWhitelist,
} from '../../../hooks/resources/useLists'
import { isValidCidrOrIp } from './ServiceForm'
import { toast } from '../../../ui/Toast/Toast'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import type { WhitelistEntryResponse, ServiceResponse } from '../../../api/types'

interface WhitelistTabProps {
  serviceId: string
  service: ServiceResponse
}

interface WhitelistFormProps {
  onSubmit: (data: { source_cidr: string }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function WhitelistForm({ onSubmit, onCancel, isSubmitting = false }: WhitelistFormProps) {
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
          placeholder="e.g. 192.168.1.0/24 or 10.0.0.1"
          autoFocus
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={isSubmitting}>
          {isSubmitting ? 'Adding...' : 'Add to Whitelist'}
        </Button>
      </div>
    </form>
  )
}

export function WhitelistTab({ serviceId, service }: WhitelistTabProps) {
  const { data: whitelist = [], isLoading, error } = useWhitelist(serviceId)
  const [isAddOpen, setIsAddOpen] = useState(false)
  const [deletingEntry, setDeletingEntry] = useState<WhitelistEntryResponse | null>(null)

  const addMutation = useAddWhitelist(serviceId)
  const removeMutation = useRemoveWhitelist(serviceId)

  const handleAddSubmit = async (payload: { source_cidr: string }) => {
    await addMutation.mutateAsync(payload)
    toast({ title: 'IP successfully whitelisted', variant: 'success' })
    setIsAddOpen(false)
  }

  const handleRemoveConfirm = async () => {
    if (!deletingEntry) return
    try {
      await removeMutation.mutateAsync(deletingEntry.source_cidr)
      toast({ title: 'IP removed from whitelist', variant: 'success' })
      setDeletingEntry(null)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to remove from whitelist', description: message, variant: 'error' })
    }
  }

  const columns = [
    {
      key: 'source_cidr',
      header: 'Source CIDR / IP Address',
      render: (entry: WhitelistEntryResponse) => (
        <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{entry.source_cidr}</span>
      ),
    },
    {
      key: 'created_at',
      header: 'Date Created',
      render: (entry: WhitelistEntryResponse) => (
        <span>{new Date(entry.created_at).toLocaleString()}</span>
      ),
    },
    {
      key: 'created_by',
      header: 'Created By',
      render: (entry: WhitelistEntryResponse) => (
        <span>{entry.created_by || 'System'}</span>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {/* VIP Ceiling Context */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: 'var(--space-4)',
      }}>
        <Card>
          <CardContent style={{ padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>VIP PPS Limit</span>
            <span style={{ fontSize: 'var(--font-size-lg)', fontWeight: 600 }}>
              {service.vip_pps != null ? service.vip_pps.toLocaleString() : 'Unlimited'}
            </span>
          </CardContent>
        </Card>
        <Card>
          <CardContent style={{ padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>VIP BPS Limit</span>
            <span style={{ fontSize: 'var(--font-size-lg)', fontWeight: 600 }}>
              {service.vip_bps != null ? `${(service.vip_bps / 1_000_000).toFixed(1)} Mbps` : 'Unlimited'}
            </span>
          </CardContent>
        </Card>
        <Card>
          <CardContent style={{ padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>Committed Bandwidth</span>
            <span style={{ fontSize: 'var(--font-size-lg)', fontWeight: 600 }}>
              {service.plan ? `${service.plan.committed_clean_gbps} Gbps` : '-'}
            </span>
          </CardContent>
        </Card>
        <Card>
          <CardContent style={{ padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 'var(--font-size-xs)' }}>Ceiling Bandwidth</span>
            <span style={{ fontSize: 'var(--font-size-lg)', fontWeight: 600 }}>
              {service.plan ? `${service.plan.ceiling_clean_gbps} Gbps` : '-'}
            </span>
          </CardContent>
        </Card>
      </div>

      <div style={{
        backgroundColor: 'var(--bg-elevated)',
        padding: 'var(--space-4)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-1)'
      }}>
        <h3 style={{ margin: 0, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>Whitelist & VIP Bypass Semantics</h3>
        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)', lineHeight: 'var(--line-height-base)' }}>
          Whitelist entries allow traffic from trusted source CIDRs to bypass scrubbing rules and rate limits entirely,
          routing directly to the service up to the service's VIP ceiling.
        </p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button variant="primary" onClick={() => setIsAddOpen(true)}>
          Add Whitelist Entry
        </Button>
      </div>

      <DataTable<WhitelistEntryResponse>
        columns={columns}
        data={whitelist}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No whitelist entries configured"
            description="Add trusted CIDRs or IP addresses to bypass scrubbing and rate-limits."
            action={
              <Button variant="primary" onClick={() => setIsAddOpen(true)}>
                Add Whitelist Entry
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
        title="Add Whitelist Entry"
      >
        <WhitelistForm
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
        title="Remove Whitelist Entry"
        description={`Are you sure you want to remove ${deletingEntry?.source_cidr} from the whitelist? This traffic will resume being scrubbed.`}
        confirmLabel="Remove"
        tone="danger"
        onConfirm={handleRemoveConfirm}
      />
    </div>
  )
}
