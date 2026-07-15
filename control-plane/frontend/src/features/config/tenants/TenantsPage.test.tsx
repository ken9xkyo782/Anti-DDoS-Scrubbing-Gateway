import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { TenantsPage } from './TenantsPage'
import {
  useTenants,
  useCreateTenant,
  useUpdateTenant,
  useDeleteTenant,
  useSuspendTenant,
  useReactivateTenant,
} from '../../../hooks/resources/useTenants'
import { ApiError } from '../../../api/errors'

vi.mock('../../../hooks/resources/useTenants', () => ({
  useTenants: vi.fn(),
  useCreateTenant: vi.fn(),
  useUpdateTenant: vi.fn(),
  useDeleteTenant: vi.fn(),
  useSuspendTenant: vi.fn(),
  useReactivateTenant: vi.fn(),
}))

describe('TenantsPage & TenantForm', () => {
  const mockCreateTenant = vi.fn()
  const mockUpdateTenant = vi.fn()
  const mockDeleteTenant = vi.fn()
  const mockSuspendTenant = vi.fn()
  const mockReactivateTenant = vi.fn()

  const defaultTenantsData = [
    {
      id: 'tenant-1',
      name: 'Acme Corp',
      status: 'active' as const,
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
      active_allocation_count: 2,
      user_count: 5,
    },
    {
      id: 'tenant-2',
      name: 'Stark Industries',
      status: 'suspended' as const,
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
      active_allocation_count: 0,
      user_count: 1,
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useTenants).mockReturnValue({
      data: defaultTenantsData,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useTenants>)

    vi.mocked(useCreateTenant).mockReturnValue({
      mutateAsync: mockCreateTenant,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateTenant>)

    vi.mocked(useUpdateTenant).mockReturnValue({
      mutateAsync: mockUpdateTenant,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateTenant>)

    vi.mocked(useDeleteTenant).mockReturnValue({
      mutateAsync: mockDeleteTenant,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteTenant>)

    vi.mocked(useSuspendTenant).mockReturnValue({
      mutateAsync: mockSuspendTenant,
      isPending: false,
    } as unknown as ReturnType<typeof useSuspendTenant>)

    vi.mocked(useReactivateTenant).mockReturnValue({
      mutateAsync: mockReactivateTenant,
      isPending: false,
    } as unknown as ReturnType<typeof useReactivateTenant>)
  })

  afterEach(() => {
    cleanup()
  })

  const renderComponent = () => {
    return render(
      <MemoryRouter>
        <TenantsPage />
      </MemoryRouter>
    )
  }

  it('renders tenants list with correct details', () => {
    renderComponent()

    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Stark Industries')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('Suspended')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument() // user count
    expect(screen.getByText('2')).toBeInTheDocument() // allocation count
  })

  it('handles tenant creation with local validation', async () => {
    renderComponent()

    fireEvent.click(screen.getByRole('button', { name: /create tenant/i }))

    const input = screen.getByLabelText(/tenant name/i)
    const submitBtn = screen.getByRole('button', { name: 'Create' })

    // Empty validation check
    fireEvent.click(submitBtn)
    expect(screen.getByText('Tenant name is required')).toBeInTheDocument()
    expect(mockCreateTenant).not.toHaveBeenCalled()

    // Success create check
    fireEvent.change(input, { target: { value: 'Wayne Enterprises' } })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockCreateTenant).toHaveBeenCalledWith({ name: 'Wayne Enterprises' })
    })
  })

  it('handles tenant status toggle (suspend and reactivate)', async () => {
    renderComponent()

    // Suspend Acme Corp (which is active)
    const suspendBtns = screen.getAllByRole('button', { name: 'Suspend' })
    fireEvent.click(suspendBtns[0])

    await waitFor(() => {
      expect(mockSuspendTenant).toHaveBeenCalled()
    })

    // Reactivate Stark Industries (which is suspended)
    const reactivateBtns = screen.getAllByRole('button', { name: 'Reactivate' })
    fireEvent.click(reactivateBtns[0])

    await waitFor(() => {
      expect(mockReactivateTenant).toHaveBeenCalled()
    })
  })

  it('shows api validation error inline on 422', async () => {
    const apiError = new ApiError(422, 'Unprocessable Entity', [
      { loc: ['body', 'name'], msg: 'Tenant name already exists', type: 'value_error' },
    ])
    mockCreateTenant.mockRejectedValueOnce(apiError)

    renderComponent()

    fireEvent.click(screen.getByRole('button', { name: /create tenant/i }))
    const input = screen.getByLabelText(/tenant name/i)
    const submitBtn = screen.getByRole('button', { name: 'Create' })

    fireEvent.change(input, { target: { value: 'Acme Corp' } })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(screen.getByText('Tenant name already exists')).toBeInTheDocument()
    })
  })

  it('triggers delete action with confirmation modal', async () => {
    renderComponent()

    const deleteBtns = screen.getAllByRole('button', { name: 'Delete' })
    fireEvent.click(deleteBtns[0])

    expect(screen.getByText(/Are you sure you want to delete tenant "Acme Corp"/i)).toBeInTheDocument()

    const confirmBtn = screen.getByRole('button', { name: 'Delete' })
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockDeleteTenant).toHaveBeenCalled()
    })
  })
})
