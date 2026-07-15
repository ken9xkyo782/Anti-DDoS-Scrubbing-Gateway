import React, { useState } from 'react'
import { Button, Field, Input } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import { isValidCidrOrIp } from '../services/ServiceForm'

interface GlobalBlacklistFormProps {
  onSubmit: (data: { source_cidr: string }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function GlobalBlacklistForm({ onSubmit, onCancel, isSubmitting = false }: GlobalBlacklistFormProps) {
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
          placeholder="e.g. 198.51.100.0/24 or 203.0.113.50"
          disabled={isSubmitting}
          autoFocus
          aria-label="Source CIDR or IP"
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          Add to Blacklist
        </Button>
      </div>
    </form>
  )
}
