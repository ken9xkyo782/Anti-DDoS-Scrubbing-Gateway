import { useState, useEffect } from 'react'
import { useAuth } from '../../../auth/AuthContext'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  EmptyState,
  ConfirmDialog,
  Badge,
  Select,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import { ApiError } from '../../../api/client'
import { useTenants } from '../../../hooks/resources/useTenants'
import {
  useAllocations,
  useMyAllocations,
  useCreateAllocation,
  useRevokeAllocation,
} from '../../../hooks/resources/useAllocations'
import { AllocationForm } from './AllocationForm'
import type { AllocationResponse, AllocationUsageResponse } from '../../../api/types'

export function AllocationsPage() {
  const { principal } = useAuth()
  const isAdmin = principal?.role === 'admin'

  if (isAdmin) {
    return <AdminAllocationsView />
  }

  return <TenantAllocationsView />
}

function TenantAllocationsView() {
  const { data: allocations = [], isLoading, error } = useMyAllocations()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="My Allocations"
        description="View CIDR ranges allocated to your tenant organization. These ranges represent address space available for your protected services."
      />

      <DataTable<AllocationResponse>
        columns={[
          {
            key: 'cidr',
            header: 'CIDR Range',
            render: (item) => <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{item.cidr}</span>,
          },
          {
            key: 'status',
            header: 'Status',
            render: (item) => (
              <Badge variant={item.status === 'active' ? 'success' : 'default'}>
                {item.status === 'active' ? 'Active' : 'Revoked'}
              </Badge>
            ),
          },
          {
            key: 'created_at',
            header: 'Allocated At',
            render: (item) => <span>{new Date(item.created_at).toLocaleString()}</span>,
          },
        ]}
        data={allocations}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No allocations found"
            description="Your tenant organization does not have any active CIDR allocations. Please contact an administrator to allocate address space."
          />
        }
      />
    </div>
  )
}

function AdminAllocationsView() {
  const { data: tenants = [], isLoading: isLoadingTenants } = useTenants()
  const [selectedTenantId, setSelectedTenantId] = useState<string>('')
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [revokingAllocation, setRevokingAllocation] = useState<AllocationResponse | null>(null)

  // Auto-select first tenant when loaded
  useEffect(() => {
    if (tenants.length > 0 && !selectedTenantId) {
      setSelectedTenantId(tenants[0].id)
    }
  }, [tenants, selectedTenantId])

  const {
    data: usageRows = [],
    isLoading: isLoadingAllocations,
    error: allocationsError,
  } = useAllocations(selectedTenantId || null)

  const createMutation = useCreateAllocation()
  const revokeMutation = useRevokeAllocation(revokingAllocation?.id ?? '', selectedTenantId || null)

  const handleCreateSubmit = async (payload: { cidr: string }) => {
    if (!selectedTenantId) return
    await createMutation.mutateAsync({
      tenant_id: selectedTenantId,
      cidr: payload.cidr,
    })
    toast({ title: 'CIDR allocated successfully', variant: 'success' })
    setIsCreateOpen(false)
  }

  const handleRevoke = async () => {
    if (!revokingAllocation) return
    try {
      await revokeMutation.mutateAsync()
      toast({ title: 'Allocation revoked successfully', variant: 'success' })
    } catch (err) {
      let message = err instanceof Error ? err.message : 'An unknown error occurred'
      if (err instanceof ApiError && err.status === 409 && err.detail && typeof err.detail === 'object') {
        const detailObj = err.detail as any
        if ('blockers' in detailObj && Array.isArray(detailObj.blockers)) {
          const serviceNames = detailObj.blockers.map((b: string) => b.replace('protected_service:', ''))
          message = `Allocation is still in use by service(s): ${serviceNames.join(', ')}`
        }
      }
      toast({
        title: 'Failed to revoke allocation',
        description: message,
        variant: 'error',
      })
    } finally {
      setRevokingAllocation(null)
    }
  }

  const selectedTenant = tenants.find((t) => t.id === selectedTenantId)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="CIDR Allocations"
        description="Manage IP/CIDR address space allocations for tenant organizations."
        actions={
          selectedTenantId &&
          usageRows.length > 0 && (
            <Button variant="primary" onClick={() => setIsCreateOpen(true)} data-testid="allocate-btn">
              Allocate CIDR
            </Button>
          )
        }
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', maxWidth: '400px' }}>
        <label htmlFor="tenant-select" style={{ fontWeight: 500, fontSize: 'var(--font-size-sm)', whiteSpace: 'nowrap' }}>
          Active Tenant:
        </label>
        <Select
          id="tenant-select"
          value={selectedTenantId}
          onValueChange={setSelectedTenantId}
          options={tenants.map((t) => ({ value: t.id, label: t.name }))}
          placeholder={isLoadingTenants ? 'Loading tenants...' : 'Select a tenant...'}
          disabled={isLoadingTenants}
        />
      </div>

      {!selectedTenantId ? (
        <EmptyState
          title="No tenant selected"
          description="Please select a tenant from the dropdown above to view and manage their CIDR allocations."
        />
      ) : (
        <DataTable<AllocationUsageResponse>
          columns={[
            {
              key: 'cidr',
              header: 'CIDR Range',
              render: (row) => (
                <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{row.allocation.cidr}</span>
              ),
            },
            {
              key: 'status',
              header: 'Status',
              render: (row) => (
                <Badge variant={row.allocation.status === 'active' ? 'success' : 'default'}>
                  {row.allocation.status === 'active' ? 'Active' : 'Revoked'}
                </Badge>
              ),
            },
            {
              key: 'usage',
              header: 'Dependent Services',
              render: (row) => <span>{row.dependent_count}</span>,
            },
            {
              key: 'created_at',
              header: 'Allocated At',
              render: (row) => <span>{new Date(row.allocation.created_at).toLocaleString()}</span>,
            },
          ]}
          data={usageRows}
          isLoading={isLoadingAllocations}
          error={allocationsError?.message}
          emptyState={
            <EmptyState
              title="No allocations found"
              description={`Tenant "${selectedTenant?.name || ''}" does not have any CIDR allocations yet.`}
              action={
                <Button variant="primary" onClick={() => setIsCreateOpen(true)} data-testid="empty-allocate-btn">
                  Allocate CIDR
                </Button>
              }
            />
          }
          rowActions={(row) => (
            <AllocationRowActions allocation={row.allocation} onRevoke={setRevokingAllocation} />
          )}
        />
      )}

      {/* Create Dialog */}
      <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen} title={`Allocate CIDR to ${selectedTenant?.name || ''}`}>
        <AllocationForm
          onSubmit={handleCreateSubmit}
          onCancel={() => setIsCreateOpen(false)}
          isSubmitting={createMutation.isPending}
        />
      </Dialog>

      {/* Revoke Confirmation Dialog */}
      <ConfirmDialog
        open={revokingAllocation !== null}
        onOpenChange={(open) => {
          if (!open) setRevokingAllocation(null)
        }}
        title="Revoke CIDR Allocation"
        description={`Are you sure you want to revoke the CIDR allocation "${revokingAllocation?.cidr}"? This will mark it as revoked and make it unavailable for new services. This action cannot be undone.`}
        confirmLabel="Revoke"
        tone="danger"
        onConfirm={handleRevoke}
      />
    </div>
  )
}

interface AllocationRowActionsProps {
  allocation: AllocationResponse
  onRevoke: (allocation: AllocationResponse) => void
}

function AllocationRowActions({ allocation, onRevoke }: AllocationRowActionsProps) {
  if (allocation.status === 'revoked') {
    return null
  }

  return (
    <Button
      variant="danger"
      size="sm"
      onClick={() => onRevoke(allocation)}
      data-testid={`revoke-btn-${allocation.cidr}`}
    >
      Revoke
    </Button>
  )
}
