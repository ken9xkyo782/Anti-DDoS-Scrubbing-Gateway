import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { ServicesPage } from './ServicesPage'
import {
  useServices,
  useCreateService,
  useUpdateService,
  useDeleteService,
  useEnableService,
  useDisableService,
} from '../../../hooks/resources/useServices'
import { useApplyStatus } from '../../../hooks/useApplyStatus'
import { ApiError } from '../../../api/errors'

// Mock resource hooks
vi.mock('../../../hooks/resources/useServices', () => ({
  useServices: vi.fn(),
  useCreateService: vi.fn(),
  useUpdateService: vi.fn(),
  useDeleteService: vi.fn(),
  useEnableService: vi.fn(),
  useDisableService: vi.fn(),
}))

vi.mock('../../../hooks/useApplyStatus', () => ({
  useApplyStatus: vi.fn(),
}))

describe('ServicesPage & ServiceForm', () => {
  const mockCreateMutate = vi.fn()
  const mockUpdateMutate = vi.fn()
  const mockDeleteMutate = vi.fn()
  const mockEnableMutate = vi.fn()
  const mockDisableMutate = vi.fn()

  const mockServicesData = [
    {
      id: 'srv-1',
      tenant_id: 'tenant-123',
      tenant_name: 'Tenant Alpha',
      created_by: 'user-1',
      creator_username: 'tenant_user',
      name: 'Alpha Backend',
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
      tenant_id: 'tenant-123',
      tenant_name: 'Tenant Alpha',
      created_by: 'user-1',
      creator_username: 'tenant_user',
      name: 'Beta DB',
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

    vi.mocked(useServices).mockReturnValue({
      data: mockServicesData,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useServices>)

    vi.mocked(useCreateService).mockReturnValue({
      mutateAsync: mockCreateMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateService>)

    vi.mocked(useUpdateService).mockReturnValue({
      mutateAsync: mockUpdateMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateService>)

    vi.mocked(useDeleteService).mockReturnValue({
      mutateAsync: mockDeleteMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteService>)

    vi.mocked(useEnableService).mockReturnValue({
      mutateAsync: mockEnableMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useEnableService>)

    vi.mocked(useDisableService).mockReturnValue({
      mutateAsync: mockDisableMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDisableService>)

    vi.mocked(useApplyStatus).mockImplementation((id: string | null) => {
      if (id === 'srv-1') {
        return {
          data: {
            service_id: 'srv-1',
            tenant_id: 'tenant-123',
            tenant_name: 'Tenant Alpha',
            apply_status: 'active',
            version: 2,
            active_version: 2,
            last_error: null,
            last_applied_at: null,
            latest_job: null,
          },
          takingLonger: false,
        } as unknown as ReturnType<typeof useApplyStatus>
      }
      if (id === 'srv-2') {
        return {
          data: {
            service_id: 'srv-2',
            tenant_id: 'tenant-123',
            tenant_name: 'Tenant Alpha',
            apply_status: 'failed',
            version: 1,
            active_version: null,
            last_error: 'Failed to deploy to scrubbing nodes',
            last_applied_at: null,
            latest_job: null,
          },
          takingLonger: false,
        } as unknown as ReturnType<typeof useApplyStatus>
      }
      return { data: undefined, takingLonger: false } as unknown as ReturnType<typeof useApplyStatus>
    })
  })

  afterEach(() => {
    cleanup()
  })

  it('lists own services and displays information', () => {
    render(
      <MemoryRouter>
        <ServicesPage />
      </MemoryRouter>
    )

    expect(screen.getByText('Alpha Backend')).toBeInTheDocument()
    expect(screen.getByText('192.168.1.0/24')).toBeInTheDocument()
    expect(screen.getByText('Beta DB')).toBeInTheDocument()
    expect(screen.getByText('10.0.0.1')).toBeInTheDocument()

    // Status badges
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('renders EmptyState with create CTA when services list is empty', () => {
    vi.mocked(useServices).mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useServices>)

    render(
      <MemoryRouter>
        <ServicesPage />
      </MemoryRouter>
    )

    expect(screen.getByText(/no services found/i)).toBeInTheDocument()
    const createBtn = screen.getByRole('button', { name: /create service/i })
    expect(createBtn).toBeInTheDocument()

    // Click CTA opens dialog
    fireEvent.click(createBtn)
    expect(screen.getByRole('heading', { name: 'Create Service' })).toBeInTheDocument()
  })

  it('validates client-side CIDR/IP and invokes create mutation on success', async () => {
    render(
      <MemoryRouter>
        <ServicesPage />
      </MemoryRouter>
    )

    const createBtn = screen.getByRole('button', { name: /create service/i })
    fireEvent.click(createBtn)

    expect(screen.getByRole('heading', { name: 'Create Service' })).toBeInTheDocument()

    const nameInput = screen.getByLabelText(/service name/i)
    const cidrInput = screen.getByLabelText(/cidr or ip address/i)
    const submitBtn = screen.getByRole('button', { name: 'Create' })

    // 1. Invalid CIDR format triggers client validation
    fireEvent.change(nameInput, { target: { value: 'New Test Service' } })
    fireEvent.change(cidrInput, { target: { value: 'invalid-cidr-format' } })
    fireEvent.click(submitBtn)

    expect(screen.getByText(/must be a valid ip address or cidr block/i)).toBeInTheDocument()
    expect(mockCreateMutate).not.toHaveBeenCalled()

    // 2. Valid CIDR format succeeds
    fireEvent.change(cidrInput, { target: { value: '192.168.10.0/24' } })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockCreateMutate).toHaveBeenCalledWith({
        name: 'New Test Service',
        cidr_or_ip: '192.168.10.0/24',
        mode: 'allow-rule-only',
        vip_pps: null,
        vip_bps: null,
      })
    })
  })

  it('surfaces inline 422 API errors appropriately', async () => {
    const apiValidationError = new ApiError(422, 'Validation error', [
      { loc: ['body', 'name'], msg: 'Service name is too short', type: 'value_error' },
    ])
    mockCreateMutate.mockRejectedValue(apiValidationError)

    render(
      <MemoryRouter>
        <ServicesPage />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /create service/i }))

    fireEvent.change(screen.getByLabelText(/service name/i), { target: { value: 'a' } })
    fireEvent.change(screen.getByLabelText(/cidr or ip address/i), { target: { value: '10.0.0.0/8' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    // Expect inline validation message from API 422 response
    await waitFor(() => {
      expect(screen.getByText('Service name is too short')).toBeInTheDocument()
    })
  })

  it('renders committed and ceiling plan limits as read-only in Edit dialog', async () => {
    render(
      <MemoryRouter>
        <ServicesPage />
      </MemoryRouter>
    )

    // Click edit row action
    const editBtn = screen.getAllByRole('button', { name: /edit/i })[0]
    fireEvent.click(editBtn)

    expect(screen.getByRole('heading', { name: 'Edit Service' })).toBeInTheDocument()

    const nameInput = screen.getByLabelText(/service name/i)
    expect(nameInput).toHaveValue('Alpha Backend')

    // Plan limits shown and readOnly
    const committedInput = screen.getByLabelText(/committed bandwidth/i)
    const ceilingInput = screen.getByLabelText(/ceiling bandwidth/i)

    expect(committedInput).toBeDisabled()
    expect(committedInput).toHaveValue(1)
    expect(ceilingInput).toBeDisabled()
    expect(ceilingInput).toHaveValue(5)
  })

  it('triggers a warning and confirm dialog when disabling a service', async () => {
    render(
      <MemoryRouter>
        <ServicesPage />
      </MemoryRouter>
    )

    // Click the toggle / status action for disabling (we have enabled services)
    const disableBtn = screen.getAllByRole('button', { name: /disable/i })[0]
    fireEvent.click(disableBtn)

    // Verify warning content about dropping all traffic
    expect(screen.getByText(/disabling this service will drop all traffic for its cidr\/ip/i)).toBeInTheDocument()
    
    // Click confirm
    const confirmBtn = screen.getByRole('button', { name: /confirm/i })
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockDisableMutate).toHaveBeenCalledWith({ confirm: true })
    })
  })
})
