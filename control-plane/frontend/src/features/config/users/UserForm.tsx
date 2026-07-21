import React, { useState } from 'react'
import { Button, Field, Input, Select } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import { useTenants } from '../../../hooks/resources/useTenants'
import type { UserResponse, Role } from '../../../api/types'

interface UserFormProps {
  user?: UserResponse
  onSubmit: (data: {
    username: string
    password?: string
    role: Role
    tenant_id?: string | null
  }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function UserForm({ user, onSubmit, onCancel, isSubmitting = false }: UserFormProps) {
  const { data: tenants = [], isLoading: isLoadingTenants } = useTenants()

  const [username, setUsername] = useState(user?.username ?? '')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>(user?.role ?? 'tenant_user')
  const [tenantId, setTenantId] = useState<string>(user?.tenant_id ?? '')

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const isEdit = !!user

  // Reset tenant selection when role changes to admin
  const handleRoleChange = (nextRole: Role) => {
    setRole(nextRole)
    if (nextRole === 'admin') {
      setTenantId('')
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!username.trim()) {
      nextErrors.username = 'Username is required'
    }
    if (!isEdit && !password) {
      nextErrors.password = 'Password is required'
    }
    if (role === 'tenant_user' && !tenantId) {
      nextErrors.tenant_id = 'Tenant assignment is required for tenant users'
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    try {
      await onSubmit({
        username: username.trim(),
        ...(isEdit ? {} : { password }),
        role,
        tenant_id: role === 'admin' ? null : tenantId || null,
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

  const roleOptions = [
    { value: 'tenant_user', label: 'Tenant User' },
    { value: 'admin', label: 'Admin' },
  ]

  const tenantOptions = [
    { value: '', label: isLoadingTenants ? 'Loading tenants...' : 'Select a tenant...' },
    ...tenants.map((t) => ({ value: t.id, label: t.name })),
  ]

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

      <Field label="Username" error={errors.username} required>
        <Input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="e.g. johndoe"
          disabled={isSubmitting}
          aria-label="Username"
        />
      </Field>

      {!isEdit && (
        <Field label="Password" error={errors.password} required>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Minimum 8 characters"
            disabled={isSubmitting}
            aria-label="Password"
          />
        </Field>
      )}

      <Field label="Role" required>
        <Select
          options={roleOptions}
          value={role}
          onValueChange={(val) => handleRoleChange(val as Role)}
          disabled={isSubmitting}
          aria-label="Role"
        />
      </Field>

      {role === 'tenant_user' && (
        <Field label="Tenant Assignment" error={errors.tenant_id} required>
          <Select
            options={tenantOptions}
            value={tenantId}
            onValueChange={setTenantId}
            disabled={isSubmitting || isLoadingTenants}
            aria-label="Tenant Assignment"
          />
        </Field>
      )}

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
