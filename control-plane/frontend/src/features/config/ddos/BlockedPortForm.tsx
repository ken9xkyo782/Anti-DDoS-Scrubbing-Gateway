import React, { useState } from 'react'
import { Button, Field, Input, NumberInput } from '../../../ui'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'

interface BlockedPortFormProps {
  onSubmit: (data: { port: number; note?: string | null }) => Promise<void>
  onCancel: () => void
  isSubmitting?: boolean
}

export function BlockedPortForm({
  onSubmit,
  onCancel,
  isSubmitting = false,
}: BlockedPortFormProps) {
  const [port, setPort] = useState<number | undefined>(undefined)
  const [note, setNote] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    if (port === undefined || isNaN(port)) {
      setErrors({ port: 'Port number is required' })
      return
    }

    if (port < 0 || port > 65535) {
      setErrors({ port: 'Port must be between 0 and 65535' })
      return
    }

    try {
      await onSubmit({ port, note: note.trim() || null })
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422 && err.detail) {
          const apiFieldErrors = fieldErrorsFrom422(err.detail)
          setErrors(apiFieldErrors)
        } else {
          const msg = typeof err.detail === 'string' ? err.detail : err.message
          setSubmitError(msg)
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

      <Field label="Port Number" error={errors.port} required>
        <NumberInput
          value={port ?? ''}
          onChange={(e) => {
            const val = e.target.value
            setPort(val === '' ? undefined : Number(val))
          }}
          min={0}
          max={65535}
          placeholder="e.g. 1900"
          disabled={isSubmitting}
          autoFocus
          aria-label="Port Number"
        />
      </Field>

      <Field label="Note / Reason" error={errors.note}>
        <Input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. SSDP amplification block"
          disabled={isSubmitting}
          aria-label="Note / Reason"
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          Block Port
        </Button>
      </div>
    </form>
  )
}
