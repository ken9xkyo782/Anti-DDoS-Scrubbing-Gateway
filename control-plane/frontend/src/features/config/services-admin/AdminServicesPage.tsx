import React, { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  EmptyState,
  ConfirmDialog,
  Select,
  Field,
  Input,
  NumberInput,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import { useTenants } from '../../../hooks/resources/useTenants'
import {
  useServices,
  useCreateService,
  useUpdateService,
  useDeleteService,
  useEnableService,
  useDisableService,
  useUpdateServicePlan,
} from '../../../hooks/resources/useServices'
import { ServiceStatusBadge } from '../services/ServiceRow'
import type { ServiceResponse } from '../../../api/types'
import { isValidCidrOrIp } from '../services/ServiceForm'

export function AdminServicesPage() {
  const { data: services = [], isLoading: isLoadingServices, error: servicesError } = useServices()
  const { data: tenants = [], isLoading: isLoadingTenants } = useTenants()

  const [selectedTenantId, setSelectedTenantId] = useState<string>('')
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [editingService, setEditingService] = useState<ServiceResponse | null>(null)
  const [sizingService, setSizingService] = useState<ServiceResponse | null>(null)
  const [deletingService, setDeletingService] = useState<ServiceResponse | null>(null)

  const createMutation = useCreateService()
  const updateMutation = useUpdateService(editingService?.id ?? '')
  const updatePlanMutation = useUpdateServicePlan(sizingService?.id ?? '')
  const deleteMutation = useDeleteService(deletingService?.id ?? '')

  // Filter services by tenant if a specific tenant is selected
  const filteredServices = selectedTenantId
    ? services.filter((s) => s.tenant_id === selectedTenantId)
    : services

  const handleCreateSubmit = async (payload: {
    name: string
    cidr_or_ip: string
    mode: string
    tenant_id: string
    vip_pps?: number | null
    vip_bps?: number | null
    committed_clean_gbps?: number | null
    ceiling_clean_gbps?: number | null
  }) => {
    const plan =
      payload.committed_clean_gbps != null && payload.ceiling_clean_gbps != null
        ? {
            committed_clean_gbps: payload.committed_clean_gbps,
            ceiling_clean_gbps: payload.ceiling_clean_gbps,
          }
        : null

    await createMutation.mutateAsync({
      name: payload.name,
      cidr_or_ip: payload.cidr_or_ip,
      mode: payload.mode,
      tenant_id: payload.tenant_id,
      vip_pps: payload.vip_pps,
      vip_bps: payload.vip_bps,
      plan,
    })

    toast({ title: 'Service created successfully', variant: 'success' })
    setIsCreateOpen(false)
  }

  const handleEditSubmit = async (payload: {
    name: string
    cidr_or_ip: string
    mode: string
    vip_pps?: number | null
    vip_bps?: number | null
  }) => {
    if (!editingService) return
    await updateMutation.mutateAsync({
      name: payload.name,
      cidr_or_ip: payload.cidr_or_ip,
      mode: payload.mode,
      vip_pps: payload.vip_pps,
      vip_bps: payload.vip_bps,
    })
    toast({ title: 'Service updated successfully', variant: 'success' })
    setEditingService(null)
  }

  const handleSizePlanSubmit = async (payload: {
    committed_clean_gbps: number
    ceiling_clean_gbps: number
  }) => {
    if (!sizingService) return
    await updatePlanMutation.mutateAsync(payload)
    toast({ title: 'Service plan updated successfully', variant: 'success' })
    setSizingService(null)
  }

  const handleDelete = async () => {
    if (!deletingService) return
    try {
      await deleteMutation.mutateAsync()
      toast({ title: 'Service deleted successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to delete service', description: message, variant: 'error' })
    } finally {
      setDeletingService(null)
    }
  }

  const tenantFilterOptions = [
    { value: '', label: 'All Tenants' },
    ...tenants.map((t) => ({ value: t.id, label: t.name })),
  ]

  const columns = [
    {
      key: 'name',
      header: 'Service Name',
      render: (service: ServiceResponse) => (
        <span style={{ fontWeight: 600 }}>{service.name}</span>
      ),
    },
    {
      key: 'tenant_name',
      header: 'Owning Tenant',
      render: (service: ServiceResponse) => (
        <span>{service.tenant_name ?? service.tenant_id}</span>
      ),
    },
    {
      key: 'cidr_or_ip',
      header: 'CIDR / IP Address',
      render: (service: ServiceResponse) => (
        <span style={{ fontFamily: 'monospace' }}>{service.cidr_or_ip}</span>
      ),
    },
    {
      key: 'mode',
      header: 'Mode',
      render: (service: ServiceResponse) => (
        <span style={{ textTransform: 'capitalize' }}>
          {service.mode.replace(/-/g, ' ')}
        </span>
      ),
    },
    {
      key: 'committed_clean_gbps',
      header: 'Committed Plan',
      render: (service: ServiceResponse) => (
        <span>{service.plan.committed_clean_gbps} Gbps</span>
      ),
    },
    {
      key: 'ceiling_clean_gbps',
      header: 'Ceiling Plan',
      render: (service: ServiceResponse) => (
        <span>{service.plan.ceiling_clean_gbps} Gbps</span>
      ),
    },
    {
      key: 'apply_status',
      header: 'Apply Status',
      render: (service: ServiceResponse) => <ServiceStatusBadge service={service} />,
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Services Oversight"
        description="Manage services across all tenant accounts and customize billing plans."
        actions={
          <Button variant="primary" onClick={() => setIsCreateOpen(true)} data-testid="create-service-btn">
            Create Service
          </Button>
        }
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', maxWidth: '400px' }}>
        <label htmlFor="tenant-filter" style={{ fontWeight: 500, fontSize: 'var(--font-size-sm)', whiteSpace: 'nowrap' }}>
          Filter by Tenant:
        </label>
        <Select
          id="tenant-filter"
          value={selectedTenantId}
          onValueChange={setSelectedTenantId}
          options={tenantFilterOptions}
          placeholder={isLoadingTenants ? 'Loading tenants...' : 'All Tenants'}
          disabled={isLoadingTenants}
        />
      </div>

      <DataTable<ServiceResponse>
        columns={columns}
        data={filteredServices}
        isLoading={isLoadingServices}
        error={servicesError?.message}
        emptyState={
          <EmptyState
            title="No services found"
            description="There are no services matching the current selection. Click create to add a new service."
            action={
              <Button variant="primary" onClick={() => setIsCreateOpen(true)} data-testid="empty-create-service-btn">
                Create Service
              </Button>
            }
          />
        }
        rowActions={(service) => (
          <AdminServiceRowActions
            service={service}
            onEdit={setEditingService}
            onSizePlan={setSizingService}
            onDelete={setDeletingService}
          />
        )}
      />

      {/* Create Dialog */}
      <Dialog
        open={isCreateOpen}
        onOpenChange={setIsCreateOpen}
        title="Create Service"
      >
        <AdminCreateServiceForm
          tenants={tenants}
          isLoadingTenants={isLoadingTenants}
          onSubmit={handleCreateSubmit}
          onCancel={() => setIsCreateOpen(false)}
          isSubmitting={createMutation.isPending}
        />
      </Dialog>

      {/* Edit Dialog */}
      <Dialog
        open={editingService !== null}
        onOpenChange={(open) => {
          if (!open) setEditingService(null)
        }}
        title="Edit Service"
      >
        {editingService && (
          <AdminEditServiceForm
            service={editingService}
            onSubmit={handleEditSubmit}
            onCancel={() => setEditingService(null)}
            isSubmitting={updateMutation.isPending}
          />
        )}
      </Dialog>

      {/* Size Plan Dialog */}
      <Dialog
        open={sizingService !== null}
        onOpenChange={(open) => {
          if (!open) setSizingService(null)
        }}
        title={`Size Plan for ${sizingService?.name || ''}`}
      >
        {sizingService && (
          <AdminSizePlanForm
            service={sizingService}
            onSubmit={handleSizePlanSubmit}
            onCancel={() => setSizingService(null)}
            isSubmitting={updatePlanMutation.isPending}
          />
        )}
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={deletingService !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingService(null)
        }}
        title="Delete Service"
        description={`Are you sure you want to delete service "${deletingService?.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDelete}
      />
    </div>
  )
}

/* --- Row Actions Component to cleanly handle scoped mutations per row --- */
interface AdminServiceRowActionsProps {
  service: ServiceResponse
  onEdit: (service: ServiceResponse) => void
  onSizePlan: (service: ServiceResponse) => void
  onDelete: (service: ServiceResponse) => void
}

function AdminServiceRowActions({
  service,
  onEdit,
  onSizePlan,
  onDelete,
}: AdminServiceRowActionsProps) {
  const [isDisableConfirmOpen, setIsDisableConfirmOpen] = useState(false)
  const enableMutation = useEnableService(service.id)
  const disableMutation = useDisableService(service.id)

  const handleEnable = async () => {
    try {
      await enableMutation.mutateAsync()
      toast({ title: 'Service enabled successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to enable service', description: message, variant: 'error' })
    }
  }

  const handleDisable = async () => {
    try {
      await disableMutation.mutateAsync({ confirm: true })
      toast({ title: 'Service disabled successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to disable service', description: message, variant: 'error' })
    } finally {
      setIsDisableConfirmOpen(false)
    }
  }

  const isMutating = enableMutation.isPending || disableMutation.isPending

  return (
    <>
      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => onEdit(service)}
          disabled={isMutating}
          data-testid={`edit-btn-${service.id}`}
        >
          Edit
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => onSizePlan(service)}
          disabled={isMutating}
          data-testid={`plan-btn-${service.id}`}
        >
          Size Plan
        </Button>

        {service.enabled ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setIsDisableConfirmOpen(true)}
            disabled={isMutating}
            data-testid={`disable-btn-${service.id}`}
          >
            Disable
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={handleEnable}
            loading={enableMutation.isPending}
            disabled={isMutating}
            data-testid={`enable-btn-${service.id}`}
          >
            Enable
          </Button>
        )}

        <Button
          variant="danger"
          size="sm"
          onClick={() => onDelete(service)}
          disabled={isMutating}
          data-testid={`delete-btn-${service.id}`}
        >
          Delete
        </Button>
      </div>

      <ConfirmDialog
        open={isDisableConfirmOpen}
        onOpenChange={setIsDisableConfirmOpen}
        title="Disable Service"
        description="Disabling this service will drop all traffic for its CIDR/IP."
        confirmLabel="Confirm"
        tone="danger"
        onConfirm={handleDisable}
      />
    </>
  )
}

/* --- Admin Create Form --- */
interface AdminCreateFormProps {
  tenants: Array<{ id: string; name: string }>
  isLoadingTenants: boolean
  onSubmit: (payload: {
    name: string
    cidr_or_ip: string
    mode: string
    tenant_id: string
    vip_pps: number | null
    vip_bps: number | null
    committed_clean_gbps: number | null
    ceiling_clean_gbps: number | null
  }) => Promise<void>
  onCancel: () => void
  isSubmitting: boolean
}

function AdminCreateServiceForm({
  tenants,
  isLoadingTenants,
  onSubmit,
  onCancel,
  isSubmitting,
}: AdminCreateFormProps) {
  const [name, setName] = useState('')
  const [cidr, setCidr] = useState('')
  const [mode, setMode] = useState<string>('allow-rule-only')
  const [tenantId, setTenantId] = useState('')
  const [vipPps, setVipPps] = useState('')
  const [vipBps, setVipBps] = useState('')

  // Sizing plan inline parameters
  const [committed, setCommitted] = useState('')
  const [ceiling, setCeiling] = useState('')

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const tenantOptions = [
    { value: '', label: isLoadingTenants ? 'Loading tenants...' : 'Select a tenant...' },
    ...tenants.map((t) => ({ value: t.id, label: t.name })),
  ]

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!name.trim()) {
      nextErrors.name = 'Service name is required'
    }
    if (!tenantId) {
      nextErrors.tenant_id = 'Tenant assignment is required'
    }
    if (!cidr.trim()) {
      nextErrors.cidr_or_ip = 'CIDR or IP address is required'
    } else if (!isValidCidrOrIp(cidr)) {
      nextErrors.cidr_or_ip = 'Must be a valid IP address or CIDR block'
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    try {
      await onSubmit({
        name: name.trim(),
        cidr_or_ip: cidr.trim(),
        mode,
        tenant_id: tenantId,
        vip_pps: vipPps ? Number(vipPps) : null,
        vip_bps: vipBps ? Number(vipBps) : null,
        committed_clean_gbps: committed ? Number(committed) : null,
        ceiling_clean_gbps: ceiling ? Number(ceiling) : null,
      })
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

      <Field label="Service Name" error={errors.name} required>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Corporate VPN Gateway"
          disabled={isSubmitting}
          aria-label="Service Name"
        />
      </Field>

      <Field label="Tenant Assignment" error={errors.tenant_id} required>
        <Select
          options={tenantOptions}
          value={tenantId}
          onValueChange={setTenantId}
          disabled={isSubmitting || isLoadingTenants}
          aria-label="Tenant Assignment"
        />
      </Field>

      <Field label="CIDR or IP Address" error={errors.cidr_or_ip} required>
        <Input
          value={cidr}
          onChange={(e) => setCidr(e.target.value)}
          placeholder="e.g. 198.51.100.0/24"
          disabled={isSubmitting}
          aria-label="CIDR or IP Address"
        />
      </Field>

      <Field label="Mode">
        <Select
          options={[{ value: 'allow-rule-only', label: 'Allow Rule Only' }]}
          value={mode}
          onValueChange={setMode}
          disabled={isSubmitting}
        />
      </Field>

      <Field label="VIP PPS Limit (Optional)">
        <NumberInput
          value={vipPps}
          onChange={(e) => setVipPps(e.target.value)}
          placeholder="e.g. 10000"
          disabled={isSubmitting}
          aria-label="VIP PPS Limit"
        />
      </Field>

      <Field label="VIP BPS Limit (Optional)">
        <NumberInput
          value={vipBps}
          onChange={(e) => setVipBps(e.target.value)}
          placeholder="e.g. 10000000"
          disabled={isSubmitting}
          aria-label="VIP BPS Limit"
        />
      </Field>

      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 'var(--space-4)' }}>
        <h4 style={{ margin: '0 0 var(--space-3) 0', fontSize: 'var(--font-size-sm)', fontWeight: 600 }}>Plan Sizing (Optional)</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
          <Field label="Committed Bandwidth (Gbps)" error={errors.committed_clean_gbps}>
            <NumberInput
              value={committed}
              onChange={(e) => setCommitted(e.target.value)}
              placeholder="e.g. 1"
              disabled={isSubmitting}
              aria-label="Committed Bandwidth"
            />
          </Field>
          <Field label="Ceiling Bandwidth (Gbps)" error={errors.ceiling_clean_gbps}>
            <NumberInput
              value={ceiling}
              onChange={(e) => setCeiling(e.target.value)}
              placeholder="e.g. 10"
              disabled={isSubmitting}
              aria-label="Ceiling Bandwidth"
            />
          </Field>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          Create
        </Button>
      </div>
    </form>
  )
}

/* --- Admin Edit Form (Basic Details only, no plan sizing) --- */
interface AdminEditFormProps {
  service: ServiceResponse
  onSubmit: (payload: {
    name: string
    cidr_or_ip: string
    mode: string
    vip_pps: number | null
    vip_bps: number | null
  }) => Promise<void>
  onCancel: () => void
  isSubmitting: boolean
}

function AdminEditServiceForm({
  service,
  onSubmit,
  onCancel,
  isSubmitting,
}: AdminEditFormProps) {
  const [name, setName] = useState(service.name)
  const [cidr, setCidr] = useState(service.cidr_or_ip)
  const [mode, setMode] = useState<string>(service.mode)
  const [vipPps, setVipPps] = useState(service.vip_pps != null ? String(service.vip_pps) : '')
  const [vipBps, setVipBps] = useState(service.vip_bps != null ? String(service.vip_bps) : '')

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!name.trim()) {
      nextErrors.name = 'Service name is required'
    }
    if (!cidr.trim()) {
      nextErrors.cidr_or_ip = 'CIDR or IP address is required'
    } else if (!isValidCidrOrIp(cidr)) {
      nextErrors.cidr_or_ip = 'Must be a valid IP address or CIDR block'
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    try {
      await onSubmit({
        name: name.trim(),
        cidr_or_ip: cidr.trim(),
        mode,
        vip_pps: vipPps ? Number(vipPps) : null,
        vip_bps: vipBps ? Number(vipBps) : null,
      })
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

      <Field label="Service Name" error={errors.name} required>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Corporate VPN Gateway"
          disabled={isSubmitting}
          aria-label="Service Name"
        />
      </Field>

      <Field label="CIDR or IP Address" error={errors.cidr_or_ip} required>
        <Input
          value={cidr}
          onChange={(e) => setCidr(e.target.value)}
          placeholder="e.g. 198.51.100.0/24"
          disabled={isSubmitting}
          aria-label="CIDR or IP Address"
        />
      </Field>

      <Field label="Mode">
        <Select
          options={[{ value: 'allow-rule-only', label: 'Allow Rule Only' }]}
          value={mode}
          onValueChange={setMode}
          disabled={isSubmitting}
        />
      </Field>

      <Field label="VIP PPS Limit (Optional)">
        <NumberInput
          value={vipPps}
          onChange={(e) => setVipPps(e.target.value)}
          placeholder="e.g. 10000"
          disabled={isSubmitting}
          aria-label="VIP PPS Limit"
        />
      </Field>

      <Field label="VIP BPS Limit (Optional)">
        <NumberInput
          value={vipBps}
          onChange={(e) => setVipBps(e.target.value)}
          placeholder="e.g. 10000000"
          disabled={isSubmitting}
          aria-label="VIP BPS Limit"
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          Save Changes
        </Button>
      </div>
    </form>
  )
}

/* --- Admin Size Plan Form --- */
interface SizePlanFormProps {
  service: ServiceResponse
  onSubmit: (payload: { committed_clean_gbps: number; ceiling_clean_gbps: number }) => Promise<void>
  onCancel: () => void
  isSubmitting: boolean
}

function AdminSizePlanForm({
  service,
  onSubmit,
  onCancel,
  isSubmitting,
}: SizePlanFormProps) {
  const [committed, setCommitted] = useState(String(service.plan.committed_clean_gbps))
  const [ceiling, setCeiling] = useState(String(service.plan.ceiling_clean_gbps))

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!committed) {
      nextErrors.committed_clean_gbps = 'Committed bandwidth is required'
    } else if (Number(committed) < 0) {
      nextErrors.committed_clean_gbps = 'Committed bandwidth cannot be negative'
    }

    if (!ceiling) {
      nextErrors.ceiling_clean_gbps = 'Ceiling bandwidth is required'
    } else if (Number(ceiling) < 0) {
      nextErrors.ceiling_clean_gbps = 'Ceiling bandwidth cannot be negative'
    } else if (Number(ceiling) < Number(committed)) {
      nextErrors.ceiling_clean_gbps = 'Ceiling bandwidth cannot be less than committed bandwidth'
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    try {
      await onSubmit({
        committed_clean_gbps: Number(committed),
        ceiling_clean_gbps: Number(ceiling),
      })
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

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
        <Field label="Committed Bandwidth (Gbps)" error={errors.committed_clean_gbps} required>
          <NumberInput
            value={committed}
            onChange={(e) => setCommitted(e.target.value)}
            placeholder="e.g. 1"
            disabled={isSubmitting}
            aria-label="Committed Bandwidth"
          />
        </Field>
        <Field label="Ceiling Bandwidth (Gbps)" error={errors.ceiling_clean_gbps} required>
          <NumberInput
            value={ceiling}
            onChange={(e) => setCeiling(e.target.value)}
            placeholder="e.g. 10"
            disabled={isSubmitting}
            aria-label="Ceiling Bandwidth"
          />
        </Field>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          Save Plan
        </Button>
      </div>
    </form>
  )
}
