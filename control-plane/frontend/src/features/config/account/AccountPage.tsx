import React, { useState } from 'react'
import { PageHeader, Card, CardHeader, CardTitle, CardContent, Field, Input, Button } from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import { apiClient, ApiError, fieldErrorsFrom422 } from '../../../api/client'

export function AccountPage() {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!currentPassword) {
      nextErrors.currentPassword = 'Current password is required'
    }
    if (!newPassword) {
      nextErrors.newPassword = 'New password is required'
    } else if (newPassword.length < 8) {
      nextErrors.newPassword = 'Password must be at least 8 characters'
    }
    if (!confirmPassword) {
      nextErrors.confirmPassword = 'Confirm new password is required'
    } else if (newPassword !== confirmPassword) {
      nextErrors.confirmPassword = 'Passwords do not match'
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    setIsSubmitting(true)
    try {
      await apiClient<void>('/auth/password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      })

      toast({ title: 'Password changed successfully', variant: 'success' })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422 && err.detail) {
          const apiFieldErrors = fieldErrorsFrom422(err.detail)
          // Map backend naming current_password/new_password to client state names
          const mappedErrors: Record<string, string> = {}
          if (apiFieldErrors.current_password) mappedErrors.currentPassword = apiFieldErrors.current_password
          if (apiFieldErrors.new_password) mappedErrors.newPassword = apiFieldErrors.new_password
          setErrors(mappedErrors)
        } else {
          setSubmitError(err.message)
        }
      } else if (err instanceof Error) {
        setSubmitError(err.message)
      } else {
        setSubmitError('An unexpected error occurred')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Account Settings"
        description="Manage your profile information and update account security settings."
      />

      <div style={{ maxWidth: '600px' }}>
        <Card>
          <CardHeader>
            <CardTitle>Change Password</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
              {submitError && (
                <div
                  style={{
                    color: 'var(--color-danger, #b42318)',
                    padding: 'var(--space-3)',
                    border: '1px solid var(--color-danger, #b42318)',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'rgba(180, 35, 24, 0.05)',
                    fontSize: 'var(--font-size-sm)',
                  }}
                  role="alert"
                >
                  {submitError}
                </div>
              )}

              <Field label="Current Password" error={errors.currentPassword} required>
                <Input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  placeholder="Enter current password"
                  disabled={isSubmitting}
                  aria-label="Current Password"
                />
              </Field>

              <Field label="New Password" error={errors.newPassword} required>
                <Input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Minimum 8 characters"
                  disabled={isSubmitting}
                  aria-label="New Password"
                />
              </Field>

              <Field label="Confirm New Password" error={errors.confirmPassword} required>
                <Input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Confirm new password"
                  disabled={isSubmitting}
                  aria-label="Confirm New Password"
                />
              </Field>

              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-2)' }}>
                <Button type="submit" variant="primary" loading={isSubmitting}>
                  Update Password
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
