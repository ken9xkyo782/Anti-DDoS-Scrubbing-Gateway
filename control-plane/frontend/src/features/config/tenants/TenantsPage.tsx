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
  useTenants,
  useCreateTenant,
  useUpdateTenant,
  useDeleteTenant,
  useSuspendTenant,
  useReactivateTenant,
} from '../../../hooks/resources/useTenants'
import { TenantForm } from './TenantForm'
import type { TenantResponse } from '../../../api/types'

export function TenantsPage() {
  const { data: tenants = [], isLoading, error } = useTenants()
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [editingTenant, setEditingTenant] = useState<TenantResponse | null>(null)
  const [deletingTenant, setDeletingTenant] = useState<TenantResponse | null>(null)

  const createMutation = useCreateTenant()
  const updateMutation = useUpdateTenant(editingTenant?.id ?? '')
  const deleteMutation = useDeleteTenant(deletingTenant?.id ?? '')

  const handleCreateSubmit = async (payload: { name: string }) => {
    await createMutation.mutateAsync(payload)
    toast({ title: 'Tenant created successfully', variant: 'success' })
    setIsCreateOpen(false)
  }

  const handleEditSubmit = async (payload: { name: string }) => {
    if (!editingTenant) return
    await updateMutation.mutateAsync(payload)
    toast({ title: 'Tenant updated successfully', variant: 'success' })
    setEditingTenant(null)
  }

  const handleDelete = async () => {
    if (!deletingTenant) return
    try {
      await deleteMutation.mutateAsync()
      toast({ title: 'Tenant deleted successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to delete tenant', description: message, variant: 'error' })
    } finally {
      setDeletingTenant(null)
    }
  }



  // Wait! In the declarations of updateMutation / suspendMutation / reactivateMutation:
  // We passed `editingTenant?.id ?? ''` / `''` to the hook creators.
  // But wait! If we do `useSuspendTenant(tenant.id)` inside a row component, or call it dynamically?
  // Let's look at how useUpdateService works: `useUpdateService(editingService?.id ?? '')`.
  // Yes! If we do it like that, then when a button is clicked, we might need a mutate call, or we can just define row actions as a subcomponent where we invoke the hooks!
  // Yes! Just like `ServiceRowActions` in `ServiceRow.tsx`, we can create a `TenantRowActions` subcomponent or inline component, which receives the tenant as a prop and calls hooks initialized with that tenant's ID.
  // Let's do that! That's extremely elegant and clean, and follows the codebase pattern perfectly.

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Tenants Management"
        description="Onboard, offboard, and manage tenant organizations and status."
        actions={
          tenants.length > 0 && (
            <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
              Create Tenant
            </Button>
          )
        }
      />

      <DataTable<TenantResponse>
        columns={[
          {
            key: 'name',
            header: 'Tenant Name',
            render: (tenant) => <span style={{ fontWeight: 600 }}>{tenant.name}</span>,
          },
          {
            key: 'status',
            header: 'Status',
            render: (tenant) => (
              <Badge variant={tenant.status === 'active' ? 'success' : 'warning'}>
                {tenant.status === 'active' ? 'Active' : 'Suspended'}
              </Badge>
            ),
          },
          {
            key: 'active_allocation_count',
            header: 'Allocations',
            render: (tenant) => <span>{tenant.active_allocation_count}</span>,
          },
          {
            key: 'user_count',
            header: 'Users',
            render: (tenant) => <span>{tenant.user_count}</span>,
          },
          {
            key: 'created_at',
            header: 'Created At',
            render: (tenant) => <span>{new Date(tenant.created_at).toLocaleDateString()}</span>,
          },
        ]}
        data={tenants}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No tenants found"
            description="Get started by creating your first tenant."
            action={
              <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
                Create Tenant
              </Button>
            }
          />
        }
        rowActions={(tenant) => (
          <TenantRowActions
            tenant={tenant}
            onEdit={setEditingTenant}
            onDelete={setDeletingTenant}
          />
        )}
      />

      {/* Create Dialog */}
      <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen} title="Create Tenant">
        <TenantForm
          onSubmit={handleCreateSubmit}
          onCancel={() => setIsCreateOpen(false)}
          isSubmitting={createMutation.isPending}
        />
      </Dialog>

      {/* Edit Dialog */}
      <Dialog
        open={editingTenant !== null}
        onOpenChange={(open) => {
          if (!open) setEditingTenant(null)
        }}
        title="Edit Tenant"
      >
        {editingTenant && (
          <TenantForm
            tenant={editingTenant}
            onSubmit={handleEditSubmit}
            onCancel={() => setEditingTenant(null)}
            isSubmitting={updateMutation.isPending}
          />
        )}
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={deletingTenant !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingTenant(null)
        }}
        title="Delete Tenant"
        description={`Are you sure you want to delete tenant "${deletingTenant?.name}"? All services and users belonging to this tenant will be deleted. This action cannot be undone.`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDelete}
      />
    </div>
  )
}

interface TenantRowActionsProps {
  tenant: TenantResponse
  onEdit: (tenant: TenantResponse) => void
  onDelete: (tenant: TenantResponse) => void
}

function TenantRowActions({ tenant, onEdit, onDelete }: TenantRowActionsProps) {
  const suspendMutation = useSuspendTenant(tenant.id)
  const reactivateMutation = useReactivateTenant(tenant.id)

  const handleToggleStatus = async () => {
    try {
      if (tenant.status === 'active') {
        await suspendMutation.mutateAsync()
        toast({ title: 'Tenant suspended successfully', variant: 'success' })
      } else {
        await reactivateMutation.mutateAsync()
        toast({ title: 'Tenant reactivated successfully', variant: 'success' })
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({
        title: `Failed to ${tenant.status === 'active' ? 'suspend' : 'reactivate'} tenant`,
        description: message,
        variant: 'error',
      })
    }
  }

  const isMutating = suspendMutation.isPending || reactivateMutation.isPending

  return (
    <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
      <Button variant="secondary" size="sm" onClick={() => onEdit(tenant)} disabled={isMutating}>
        Edit
      </Button>
      <Button variant="secondary" size="sm" onClick={handleToggleStatus} loading={isMutating}>
        {tenant.status === 'active' ? 'Suspend' : 'Reactivate'}
      </Button>
      <Button variant="danger" size="sm" onClick={() => onDelete(tenant)} disabled={isMutating}>
        Delete
      </Button>
    </div>
  )
}
