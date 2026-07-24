import React, { useState } from 'react'
import { Button, Field, Input } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import { useCheckOverlap } from '../../../hooks/resources/useAllocations'

interface AllocationFormProps {
  onSubmit: (data: { cidr: string }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function AllocationForm({ onSubmit, onCancel, isSubmitting = false }: AllocationFormProps) {
  const [cidr, setCidr] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isCheckingOverlap, setIsCheckingOverlap] = useState(false)

  const checkOverlapMutation = useCheckOverlap()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const trimmed = cidr.trim()
    if (!trimmed) {
      setErrors({ cidr: 'CIDR is required' })
      return
    }

    setIsCheckingOverlap(true)

    // Run overlap check synchronously when Allocate button is clicked
    try {
      const res = await checkOverlapMutation.mutateAsync({ cidr: trimmed })
      if (res.overlaps && res.conflicts.length > 0) {
        const conflictsStr = res.conflicts.map((c) => c.cidr).join(', ')
        setSubmitError(`Failed: CIDR overlaps with existing allocations (${conflictsStr})`)
        return
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422 && err.detail) {
          const apiFieldErrors = fieldErrorsFrom422(err.detail)
          setErrors(apiFieldErrors)
          return
        } else {
          setSubmitError(err.message)
          return
        }
      }
    } finally {
      setIsCheckingOverlap(false)
    }

    try {
      await onSubmit({ cidr: trimmed })
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

  const isLoading = isSubmitting || isCheckingOverlap

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div
          style={{
            color: 'var(--color-danger, #b42318)',
            padding: 'var(--space-3)',
            border: '1px solid var(--color-danger-border, #fecdca)',
            backgroundColor: 'var(--color-danger-bg, #fef3f2)',
            borderRadius: 'var(--radius-md)',
            fontSize: 'var(--font-size-sm)',
          }}
          role="alert"
          data-testid="submit-error"
        >
          {submitError}
        </div>
      )}

      <Field label="CIDR Range" error={errors.cidr} required>
        <Input
          value={cidr}
          onChange={(e) => setCidr(e.target.value)}
          placeholder="e.g. 203.0.113.0/24"
          disabled={isLoading}
          aria-label="CIDR Range"
          data-testid="cidr-input"
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isLoading}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isLoading}>
          Allocate
        </Button>
      </div>
    </form>
  )
}

