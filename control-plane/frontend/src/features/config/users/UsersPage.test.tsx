import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { UsersPage } from './UsersPage'
import {
  useUsers,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
  useResetPassword,
} from '../../../hooks/resources/useUsers'
import { useTenants } from '../../../hooks/resources/useTenants'


vi.mock('../../../hooks/resources/useUsers', () => ({
  useUsers: vi.fn(),
  useCreateUser: vi.fn(),
  useUpdateUser: vi.fn(),
  useDeleteUser: vi.fn(),
  useResetPassword: vi.fn(),
}))

vi.mock('../../../hooks/resources/useTenants', () => ({
  useTenants: vi.fn(),
}))

vi.mock('../../../ui', async () => {
  const actual = await vi.importActual<typeof import('../../../ui')>('../../../ui')
  return {
    ...actual,
    Select: ({
      options,
      value,
      onValueChange,
      'aria-label': ariaLabel,
      disabled,
    }: {
      options: { value: string; label: string }[]
      value?: string
      onValueChange?: (value: string) => void
      'aria-label'?: string
      disabled?: boolean
    }) => (
      <select
        value={value ?? ''}
        onChange={(e) => onValueChange && onValueChange(e.target.value)}
        aria-label={ariaLabel}
        disabled={disabled}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    )
  }
})


describe('UsersPage & UserForm & ResetPasswordDialog', () => {
  const mockCreateUser = vi.fn()
  const mockUpdateUser = vi.fn()
  const mockDeleteUser = vi.fn()
  const mockResetPassword = vi.fn()

  const defaultUsersData = [
    {
      id: 'user-1',
      username: 'admin_user',
      role: 'admin' as const,
      tenant_id: null,
      tenant_name: null,
      status: 'active' as const,
      last_login_at: '2026-07-15T01:00:00Z',
    },
    {
      id: 'user-2',
      username: 'tenant_operator',
      role: 'tenant_user' as const,
      tenant_id: 'tenant-123',
      tenant_name: 'Acme Corp',
      status: 'active' as const,
      last_login_at: null,
    },
  ]

  const defaultTenantsData = [
    {
      id: 'tenant-123',
      name: 'Acme Corp',
      status: 'active' as const,
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
      active_allocation_count: 1,
      user_count: 1,
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useUsers).mockReturnValue({
      data: defaultUsersData,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useUsers>)

    vi.mocked(useTenants).mockReturnValue({
      data: defaultTenantsData,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useTenants>)

    vi.mocked(useCreateUser).mockReturnValue({
      mutateAsync: mockCreateUser,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateUser>)

    vi.mocked(useUpdateUser).mockReturnValue({
      mutateAsync: mockUpdateUser,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateUser>)

    vi.mocked(useDeleteUser).mockReturnValue({
      mutateAsync: mockDeleteUser,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteUser>)

    vi.mocked(useResetPassword).mockReturnValue({
      mutateAsync: mockResetPassword,
      isPending: false,
    } as unknown as ReturnType<typeof useResetPassword>)
  })

  afterEach(() => {
    cleanup()
  })

  const renderComponent = () => {
    return render(
      <MemoryRouter>
        <UsersPage />
      </MemoryRouter>
    )
  }

  it('renders users list with correct details', () => {
    renderComponent()

    expect(screen.getByText('admin_user')).toBeInTheDocument()
    expect(screen.getByText('tenant_operator')).toBeInTheDocument()
    expect(screen.getByText('Admin')).toBeInTheDocument()
    expect(screen.getByText('Tenant User')).toBeInTheDocument()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getAllByText('Active').length).toBeGreaterThan(0)
  })

  it('handles user creation and conditional tenant selection', async () => {
    renderComponent()

    fireEvent.click(screen.getByRole('button', { name: /create user/i }))

    const usernameInput = screen.getByLabelText(/username/i)
    const passwordInput = screen.getByLabelText(/password/i)
    const roleSelect = screen.getByLabelText(/role/i)
    const submitBtn = screen.getByRole('button', { name: 'Create' })

    // 1. Initial State: Role is tenant_user, so Tenant Assignment selector is visible and required
    expect(screen.getByLabelText(/tenant assignment/i)).toBeInTheDocument()
    
    // Trigger submit empty validations
    fireEvent.click(submitBtn)
    expect(screen.getByText('Username is required')).toBeInTheDocument()
    expect(screen.getByText('Password is required')).toBeInTheDocument()
    expect(screen.getByText('Tenant assignment is required for tenant users')).toBeInTheDocument()
    expect(mockCreateUser).not.toHaveBeenCalled()

    // 2. Select Admin Role: Tenant Assignment is hidden/cleared
    fireEvent.change(roleSelect, { target: { value: 'admin' } })
    expect(screen.queryByLabelText(/tenant assignment/i)).not.toBeInTheDocument()

    // Submit as admin
    fireEvent.change(usernameInput, { target: { value: 'superadmin' } })
    fireEvent.change(passwordInput, { target: { value: 'secretpwd123' } })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockCreateUser).toHaveBeenCalledWith({
        username: 'superadmin',
        password: 'secretpwd123',
        role: 'admin',
        tenant_id: null,
      })
    })
  })

  it('handles user status toggle (disable and enable)', async () => {
    renderComponent()

    const disableBtns = screen.getAllByRole('button', { name: 'Disable' })
    fireEvent.click(disableBtns[0])

    await waitFor(() => {
      expect(mockUpdateUser).toHaveBeenCalledWith({ status: 'disabled' })
    })
  })

  it('handles password resets via password dialog', async () => {
    renderComponent()

    const resetBtns = screen.getAllByRole('button', { name: 'Reset Pwd' })
    fireEvent.click(resetBtns[0])

    expect(screen.getByText(/Resetting password for user/i)).toHaveTextContent('admin_user')

    const newPwdInput = screen.getByLabelText(/new password/i)
    const submitBtn = screen.getByRole('button', { name: 'Reset Password' })

    // validation empty password check
    fireEvent.click(submitBtn)
    expect(screen.getByText('Password is required')).toBeInTheDocument()
    expect(mockResetPassword).not.toHaveBeenCalled()

    // successful reset submit
    fireEvent.change(newPwdInput, { target: { value: 'brandnewpass123' } })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockResetPassword).toHaveBeenCalledWith({ new_password: 'brandnewpass123' })
    })
  })

  it('handles user deletions with confirmation modal', async () => {
    renderComponent()

    const deleteBtns = screen.getAllByRole('button', { name: 'Delete' })
    fireEvent.click(deleteBtns[0])

    expect(screen.getByText(/Are you sure you want to delete user "admin_user"/i)).toBeInTheDocument()

    const confirmBtn = screen.getByRole('button', { name: 'Delete' })
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockDeleteUser).toHaveBeenCalled()
    })
  })
})
