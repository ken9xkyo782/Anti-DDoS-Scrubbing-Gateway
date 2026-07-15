import React, { useState } from 'react'
import { Button, Field, Input, Dialog } from '../../../ui'
import { ApiError } from '../../../api/client'
import type { UserResponse } from '../../../api/types'

interface ResetPasswordDialogProps {
  user: UserResponse | null
  onClose: () => void
  onSubmit: (password: string) => Promise<void>
  isSubmitting?: boolean
}

export function ResetPasswordDialog({ user, onClose, onSubmit, isSubmitting = false }: ResetPasswordDialogProps) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!password) {
      setError('Password is required')
      return
    }

    try {
      await onSubmit(password)
      setPassword('')
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('An unexpected error occurred')
      }
    }
  }

  return (
    <Dialog open={user !== null} onOpenChange={(open) => { if (!open) onClose() }} title="Reset Password">
      {user && (
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <p style={{ color: 'var(--text-muted)' }}>
            Resetting password for user <strong style={{ color: 'var(--text-color)' }}>{user.username}</strong>.
          </p>

          {error && (
            <div
              style={{
                color: 'var(--color-danger, #b42318)',
                padding: 'var(--space-2)',
                border: '1px solid',
                borderRadius: 'var(--radius-md)',
              }}
              role="alert"
            >
              {error}
            </div>
          )}

          <Field label="New Password" required>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Minimum 8 characters"
              disabled={isSubmitting}
              aria-label="New Password"
            />
          </Field>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
            <Button type="button" variant="secondary" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={isSubmitting}>
              Reset Password
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  )
}
