import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AdminServicesPage } from './AdminServicesPage'
import {
  useServices,
  useCreateService,
  useUpdateService,
  useDeleteService,
  useEnableService,
  useDisableService,
  useUpdateServicePlan,
} from '../../../hooks/resources/useServices'
import { useTenants } from '../../../hooks/resources/useTenants'
import { useApplyStatus } from '../../../hooks/useApplyStatus'

// Mock resource hooks
vi.mock('../../../hooks/resources/useServices', () => ({
  useServices: vi.fn(),
  useCreateService: vi.fn(),
  useUpdateService: vi.fn(),
  useDeleteService: vi.fn(),
  useEnableService: vi.fn(),
  useDisableService: vi.fn(),
  useUpdateServicePlan: vi.fn(),
}))

vi.mock('../../../hooks/resources/useTenants', () => ({
  useTenants: vi.fn(),
}))

vi.mock('../../../hooks/useApplyStatus', () => ({
  useApplyStatus: vi.fn(),
}))

// Mock Select component to render as a native HTML select to facilitate testing-library interactions
vi.mock('../../../ui', async () => {
  const actual = await vi.importActual<typeof import('../../../ui')>('../../../ui')
  return {
    ...actual,
    Select: ({
      options,
      value,
      onValueChange,
      'aria-label': ariaLabel,
      id,
      disabled,
    }: {
      options: { value: string; label: string }[]
      value?: string
      onValueChange?: (value: string) => void
      'aria-label'?: string
      id?: string
      disabled?: boolean
    }) => (
      <select
        id={id}
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

describe('AdminServicesPage', () => {
  const mockCreateMutate = vi.fn()
  const mockUpdateMutate = vi.fn()
  const mockUpdatePlanMutate = vi.fn()
  const mockDeleteMutate = vi.fn()
  const mockEnableMutate = vi.fn()
  const mockDisableMutate = vi.fn()

  const defaultTenants = [
    { id: 'tenant-1', name: 'Tenant Alpha', status: 'active' },
    { id: 'tenant-2', name: 'Tenant Beta', status: 'active' },
  ]

  const mockServicesData = [
    {
      id: 'srv-1',
      tenant_id: 'tenant-1',
      tenant_name: 'Tenant Alpha',
      created_by: 'user-1',
      creator_username: 'tenant_user_1',
      name: 'Alpha Gateway',
      cidr_or_ip: '192.168.1.0/24',
      mode: 'allow-rule-only' as const,
      enabled: true,
      vip_pps: 1000,
      vip_bps: 1000000,
      apply_status: 'active' as const,
      version: 2,
      active_version: 2,
      plan: {
        committed_clean_gbps: 1,
        ceiling_clean_gbps: 5,
        billing_metric: 'clean-traffic',
        overage_policy: 'drop',
      },
      warnings: [],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    },
    {
      id: 'srv-2',
      tenant_id: 'tenant-2',
      tenant_name: 'Tenant Beta',
      created_by: 'user-2',
      creator_username: 'tenant_user_2',
      name: 'Beta Backend',
      cidr_or_ip: '10.0.0.1',
      mode: 'allow-rule-only' as const,
      enabled: false,
      vip_pps: null,
      vip_bps: null,
      apply_status: 'failed' as const,
      version: 1,
      active_version: null,
      plan: {
        committed_clean_gbps: 2,
        ceiling_clean_gbps: 10,
        billing_metric: 'clean-traffic',
        overage_policy: 'drop',
      },
      warnings: [],
      created_at: '2026-07-15T01:00:00Z',
      updated_at: '2026-07-15T01:00:00Z',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useTenants).mockReturnValue({
      data: defaultTenants,
      isLoading: false,
      error: null,
    } as never)

    vi.mocked(useServices).mockReturnValue({
      data: mockServicesData,
      isLoading: false,
      isError: false,
      error: null,
    } as never)

    vi.mocked(useCreateService).mockReturnValue({
      mutateAsync: mockCreateMutate,
      isPending: false,
    } as never)

    vi.mocked(useUpdateService).mockReturnValue({
      mutateAsync: mockUpdateMutate,
      isPending: false,
    } as never)

    vi.mocked(useUpdateServicePlan).mockReturnValue({
      mutateAsync: mockUpdatePlanMutate,
      isPending: false,
    } as never)

    vi.mocked(useDeleteService).mockReturnValue({
      mutateAsync: mockDeleteMutate,
      isPending: false,
    } as never)

    vi.mocked(useEnableService).mockReturnValue({
      mutateAsync: mockEnableMutate,
      isPending: false,
    } as never)

    vi.mocked(useDisableService).mockReturnValue({
      mutateAsync: mockDisableMutate,
      isPending: false,
    } as never)

    vi.mocked(useApplyStatus).mockImplementation((id: string | null) => {
      if (id === 'srv-1') {
        return {
          data: {
            service_id: 'srv-1',
            tenant_id: 'tenant-1',
            tenant_name: 'Tenant Alpha',
            apply_status: 'active',
            version: 2,
            active_version: 2,
            last_error: null,
            last_applied_at: null,
            latest_job: null,
          },
          takingLonger: false,
        } as never
      }
      if (id === 'srv-2') {
        return {
          data: {
            service_id: 'srv-2',
            tenant_id: 'tenant-2',
            tenant_name: 'Tenant Beta',
            apply_status: 'failed',
            version: 1,
            active_version: null,
            last_error: 'Failed to deploy to scrubbing nodes',
            last_applied_at: null,
            latest_job: null,
          },
          takingLonger: false,
        } as never
      }
      return { data: undefined, takingLonger: false } as never
    })
  })

  afterEach(() => {
    cleanup()
  })

  const renderComponent = () => {
    return render(
      <MemoryRouter>
        <AdminServicesPage />
      </MemoryRouter>
    )
  }

  it('lists services across tenants with owning tenant', () => {
    renderComponent()

    expect(screen.getByText('Services Oversight')).toBeInTheDocument()
    expect(screen.getByText('Alpha Gateway')).toBeInTheDocument()
    expect(screen.getAllByText('Tenant Alpha').length).toBeGreaterThan(0)
    expect(screen.getByText('192.168.1.0/24')).toBeInTheDocument()
    expect(screen.getByText('1 Gbps')).toBeInTheDocument()

    expect(screen.getByText('Beta Backend')).toBeInTheDocument()
    expect(screen.getAllByText('Tenant Beta').length).toBeGreaterThan(0)
    expect(screen.getByText('10.0.0.1')).toBeInTheDocument()
    expect(screen.getByText('10 Gbps')).toBeInTheDocument()
  })

  it('filters services by selected tenant', async () => {
    renderComponent()

    expect(screen.getByText('Alpha Gateway')).toBeInTheDocument()
    expect(screen.getByText('Beta Backend')).toBeInTheDocument()

    const select = screen.getByLabelText('Filter by Tenant:')
    fireEvent.change(select, { target: { value: 'tenant-1' } })

    // Only Alpha Gateway should remain
    expect(screen.getByText('Alpha Gateway')).toBeInTheDocument()
    expect(screen.queryByText('Beta Backend')).not.toBeInTheDocument()
  })

  it('validates and requires tenant_id and fields on admin create, and supports plan inline', async () => {
    renderComponent()

    const createBtn = screen.getByTestId('create-service-btn')
    fireEvent.click(createBtn)

    expect(screen.getByRole('heading', { name: 'Create Service' })).toBeInTheDocument()

    const submitBtn = screen.getByRole('button', { name: 'Create' })
    fireEvent.click(submitBtn)

    // Verify validation errors
    expect(screen.getByText('Service name is required')).toBeInTheDocument()
    expect(screen.getByText('Tenant assignment is required')).toBeInTheDocument()
    expect(screen.getByText('CIDR or IP address is required')).toBeInTheDocument()

    // Fill in required fields
    fireEvent.change(screen.getByLabelText('Service Name'), { target: { value: 'New Admin Service' } })
    fireEvent.change(screen.getByLabelText('Tenant Assignment'), { target: { value: 'tenant-2' } })
    fireEvent.change(screen.getByLabelText('CIDR or IP Address'), { target: { value: '192.0.2.0/24' } })

    // Optional plan fields (match by aria-label name)
    fireEvent.change(screen.getByLabelText('Committed Bandwidth'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Ceiling Bandwidth'), { target: { value: '25' } })

    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockCreateMutate).toHaveBeenCalledWith({
        name: 'New Admin Service',
        cidr_or_ip: '192.0.2.0/24',
        mode: 'allow-rule-only',
        tenant_id: 'tenant-2',
        vip_pps: null,
        vip_bps: null,
        plan: {
          committed_clean_gbps: 5,
          ceiling_clean_gbps: 25,
        },
      })
    })
  })

  it('supports plan sizing (PATCH /services/{id}/plan)', async () => {
    renderComponent()

    const planBtn = screen.getByTestId('plan-btn-srv-1')
    fireEvent.click(planBtn)

    expect(screen.getByRole('heading', { name: 'Size Plan for Alpha Gateway' })).toBeInTheDocument()

    const committedInput = screen.getByLabelText('Committed Bandwidth')
    const ceilingInput = screen.getByLabelText('Ceiling Bandwidth')

    expect(committedInput).toHaveValue(1)
    expect(ceilingInput).toHaveValue(5)

    fireEvent.change(committedInput, { target: { value: '10' } })
    fireEvent.change(ceilingInput, { target: { value: '50' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save Plan' }))

    await waitFor(() => {
      expect(mockUpdatePlanMutate).toHaveBeenCalledWith({
        committed_clean_gbps: 10,
        ceiling_clean_gbps: 50,
      })
    })
  })

  it('executes enable, disable, and delete lifecycle actions correctly', async () => {
    renderComponent()

    // 1. Enable disabled service (srv-2)
    const enableBtn = screen.getByTestId('enable-btn-srv-2')
    fireEvent.click(enableBtn)
    await waitFor(() => {
      expect(mockEnableMutate).toHaveBeenCalled()
    })

    // 2. Disable enabled service (srv-1)
    const disableBtn = screen.getByTestId('disable-btn-srv-1')
    fireEvent.click(disableBtn)
    expect(screen.getByText('Disable Service')).toBeInTheDocument()
    expect(screen.getByText(/disabling this service will drop all traffic/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => {
      expect(mockDisableMutate).toHaveBeenCalledWith({ confirm: true })
    })

    // 3. Delete service (srv-2)
    const deleteBtn = screen.getByTestId('delete-btn-srv-2')
    fireEvent.click(deleteBtn)
    expect(screen.getByText('Delete Service')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      expect(mockDeleteMutate).toHaveBeenCalled()
    })
  })
})
