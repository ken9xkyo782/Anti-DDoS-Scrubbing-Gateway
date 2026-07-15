import React, { useState } from 'react'
import { Button, Field, Input } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import type { TenantResponse } from '../../../api/types'

interface TenantFormProps {
  tenant?: TenantResponse
  onSubmit: (data: { name: string }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function TenantForm({ tenant, onSubmit, onCancel, isSubmitting = false }: TenantFormProps) {
  const [name, setName] = useState(tenant?.name ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    if (!name.trim()) {
      setErrors({ name: 'Tenant name is required' })
      return
    }

    try {
      await onSubmit({ name: name.trim() })
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

  const isEdit = !!tenant

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div
          style={{
            color: 'var(--color-danger, #b42318)',
            padding: 'var(--space-2)',
            border: '1px solid',
            borderRadius: 'var(--radius-md)',
          }}
          role="alert"
        >
          {submitError}
        </div>
      )}

      <Field label="Tenant Name" error={errors.name} required>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Acme Corp"
          disabled={isSubmitting}
          aria-label="Tenant Name"
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          {isEdit ? 'Save Changes' : 'Create'}
        </Button>
      </div>
    </form>
  )
}
