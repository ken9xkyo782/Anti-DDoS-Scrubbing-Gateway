import { useState } from 'react'
import { Button, ConfirmDialog } from '../../../ui'
import type { ServiceResponse } from '../../../api/types'
import { useEnableService, useDisableService, useDeleteService } from '../../../hooks/resources/useServices'
import { useApplyStatus } from '../../../hooks/useApplyStatus'
import { StatusBadge } from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'

export function ServiceStatusBadge({ service }: { service: ServiceResponse }) {
  const { data } = useApplyStatus(service.id)
  const status = data?.apply_status ?? service.apply_status
  return <StatusBadge status={status} />
}

interface ServiceRowActionsProps {
  service: ServiceResponse
  onEdit: (service: ServiceResponse) => void
}

export function ServiceRowActions({ service, onEdit }: ServiceRowActionsProps) {
  const [isDisableConfirmOpen, setIsDisableConfirmOpen] = useState(false)
  const [isDeleteConfirmOpen, setIsDeleteConfirmOpen] = useState(false)

  const enableMutation = useEnableService(service.id)
  const disableMutation = useDisableService(service.id)
  const deleteMutation = useDeleteService(service.id)

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
    }
  }

  const handleDelete = async () => {
    try {
      await deleteMutation.mutateAsync()
      toast({ title: 'Service deleted successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to delete service', description: message, variant: 'error' })
    }
  }

  return (
    <>
      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
        <Button variant="secondary" size="sm" onClick={() => onEdit(service)}>
          Edit
        </Button>

        {service.enabled ? (
          <Button variant="secondary" size="sm" onClick={() => setIsDisableConfirmOpen(true)}>
            Disable
          </Button>
        ) : (
          <Button variant="primary" size="sm" onClick={handleEnable}>
            Enable
          </Button>
        )}

        <Button variant="danger" size="sm" onClick={() => setIsDeleteConfirmOpen(true)}>
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

      <ConfirmDialog
        open={isDeleteConfirmOpen}
        onOpenChange={setIsDeleteConfirmOpen}
        title="Delete Service"
        description={`Are you sure you want to delete service "${service.name}"? This action cannot be undone.`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDelete}
      />
    </>
  )
}
