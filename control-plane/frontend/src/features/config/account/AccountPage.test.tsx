import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AccountPage } from './AccountPage'
import { apiClient, ApiError } from '../../../api/client'

vi.mock('../../../api/client', () => ({
  apiClient: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string, public detail?: unknown) {
      super(message)
    }
  },
  fieldErrorsFrom422: (detail: unknown): Record<string, string> => {
    if (Array.isArray(detail)) {
      const errs: Record<string, string> = {}
      detail.forEach((err: { loc: (string | number)[]; msg: string }) => {
        const fieldName = String(err.loc[err.loc.length - 1])
        errs[fieldName] = err.msg
      })
      return errs
    }
    return {}
  },
}))

vi.mock('../../../ui/Toast/Toast', () => ({
  toast: vi.fn(),
}))

describe('AccountPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('renders account settings page with form fields', () => {
    render(<AccountPage />)

    expect(screen.getByText('Account Settings')).toBeDefined()
    expect(screen.getByText('Change Password')).toBeDefined()
    expect(screen.getByLabelText('Current Password')).toBeDefined()
    expect(screen.getByLabelText('New Password')).toBeDefined()
    expect(screen.getByLabelText('Confirm New Password')).toBeDefined()
    expect(screen.getByRole('button', { name: /update password/i })).toBeDefined()
  })

  it('shows client-side validation errors for empty fields', async () => {
    render(<AccountPage />)

    fireEvent.click(screen.getByRole('button', { name: /update password/i }))

    expect(screen.getByText('Current password is required')).toBeDefined()
    expect(screen.getByText('New password is required')).toBeDefined()
    expect(screen.getByText('Confirm new password is required')).toBeDefined()
    expect(apiClient).not.toHaveBeenCalled()
  })

  it('shows client-side validation errors for short password and mismatch', async () => {
    render(<AccountPage />)

    fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: 'oldpass123' } })
    fireEvent.change(screen.getByLabelText('New Password'), { target: { value: 'short' } })
    fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: 'mismatch' } })

    fireEvent.click(screen.getByRole('button', { name: /update password/i }))

    expect(screen.queryByText('Current password is required')).toBeNull()
    expect(screen.getByText('Password must be at least 8 characters')).toBeDefined()
    expect(screen.getByText('Passwords do not match')).toBeDefined()
    expect(apiClient).not.toHaveBeenCalled()
  })

  it('submits successfully, triggers toast, and clears inputs', async () => {
    vi.mocked(apiClient).mockResolvedValue(undefined)
    render(<AccountPage />)

    const currentInput = screen.getByLabelText('Current Password')
    const newInput = screen.getByLabelText('New Password')
    const confirmInput = screen.getByLabelText('Confirm New Password')

    fireEvent.change(currentInput, { target: { value: 'current123' } })
    fireEvent.change(newInput, { target: { value: 'newpassword123' } })
    fireEvent.change(confirmInput, { target: { value: 'newpassword123' } })

    fireEvent.click(screen.getByRole('button', { name: /update password/i }))

    await waitFor(() => {
      expect(apiClient).toHaveBeenCalledWith('/auth/password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          current_password: 'current123',
          new_password: 'newpassword123',
        }),
      })
    })

    // Check inputs are cleared
    expect((currentInput as HTMLInputElement).value).toBe('')
    expect((newInput as HTMLInputElement).value).toBe('')
    expect((confirmInput as HTMLInputElement).value).toBe('')
  })

  it('surfaces general API errors', async () => {
    vi.mocked(apiClient).mockRejectedValue(new ApiError(400, 'Invalid current password'))
    render(<AccountPage />)

    fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: 'wrongpass' } })
    fireEvent.change(screen.getByLabelText('New Password'), { target: { value: 'newpassword123' } })
    fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: 'newpassword123' } })

    fireEvent.click(screen.getByRole('button', { name: /update password/i }))

    await waitFor(() => {
      expect(screen.getByText('Invalid current password')).toBeDefined()
    })
  })

  it('surfaces backend 422 field errors', async () => {
    vi.mocked(apiClient).mockRejectedValue(
      new ApiError(422, 'Unprocessable Entity', [
        {
          loc: ['body', 'current_password'],
          msg: 'Must be different from existing password',
          type: 'value_error',
        },
      ])
    )
    render(<AccountPage />)

    fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: 'samepassword123' } })
    fireEvent.change(screen.getByLabelText('New Password'), { target: { value: 'samepassword123' } })
    fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: 'samepassword123' } })

    fireEvent.click(screen.getByRole('button', { name: /update password/i }))

    await waitFor(() => {
      expect(screen.getByText('Must be different from existing password')).toBeDefined()
    })
  })
})
