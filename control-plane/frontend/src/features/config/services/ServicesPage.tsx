import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  EmptyState,
} from '../../../ui'
import {
  useServices,
  useCreateService,
  useUpdateService,
} from '../../../hooks/resources/useServices'
import { ServiceForm } from './ServiceForm'
import { ServiceStatusBadge, ServiceRowActions } from './ServiceRow'
import type { ServiceResponse } from '../../../api/types'

interface ServiceFormPayload {
  name: string
  cidr_or_ip: string
  mode: string
  vip_pps?: number | null
  vip_bps?: number | null
  service_pps?: number | null
  service_bps?: number | null
}

export function ServicesPage() {
  const { data: services = [], isLoading, error } = useServices()
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [editingService, setEditingService] = useState<ServiceResponse | null>(null)

  const createMutation = useCreateService()
  const updateMutation = useUpdateService(editingService?.id ?? '')

  const handleCreateSubmit = async (payload: ServiceFormPayload) => {
    await createMutation.mutateAsync({
      name: payload.name,
      cidr_or_ip: payload.cidr_or_ip,
      mode: payload.mode,
      vip_pps: payload.vip_pps,
      vip_bps: payload.vip_bps,
      service_pps: payload.service_pps,
      service_bps: payload.service_bps,
    })
    setIsCreateOpen(false)
  }

  const handleEditSubmit = async (payload: ServiceFormPayload) => {
    if (!editingService) return
    await updateMutation.mutateAsync({
      name: payload.name,
      cidr_or_ip: payload.cidr_or_ip,
      mode: payload.mode,
      vip_pps: payload.vip_pps,
      vip_bps: payload.vip_bps,
      service_pps: payload.service_pps,
      service_bps: payload.service_bps,
    })
    setEditingService(null)
  }

  const columns = [
    {
      key: 'name',
      header: 'Service Name',
      render: (service: ServiceResponse) => (
        <Link
          to={`/services/${service.id}`}
          style={{
            fontWeight: 600,
            color: 'var(--color-primary, #0969da)',
            textDecoration: 'none',
          }}
        >
          {service.name}
        </Link>
      ),
    },
    {
      key: 'cidr_or_ip',
      header: 'CIDR / IP Address',
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
      key: 'apply_status',
      header: 'Apply Status',
      render: (service: ServiceResponse) => <ServiceStatusBadge service={service} />,
    },
  ]

  const emptyStateAction = (
    <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
      Create Service
    </Button>
  )

  const headerActions = services.length > 0 && (
    <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
      Create Service
    </Button>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="My Services"
        description="Configure and manage scrubbed IP addresses and network blocks for your tenant account."
        actions={headerActions}
      />

      <DataTable<ServiceResponse>
        columns={columns}
        data={services}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No services found"
            description="You don't have any configured services yet. Get started by creating your first service."
            action={emptyStateAction}
          />
        }
        rowActions={(service) => (
          <ServiceRowActions service={service} onEdit={setEditingService} />
        )}
      />

      {/* Create Dialog */}
      <Dialog
        open={isCreateOpen}
        onOpenChange={setIsCreateOpen}
        title="Create Service"
      >
        <ServiceForm
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
          <ServiceForm
            service={editingService}
            onSubmit={handleEditSubmit}
            onCancel={() => setEditingService(null)}
            isSubmitting={updateMutation.isPending}
          />
        )}
      </Dialog>
    </div>
  )
}
