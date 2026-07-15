import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { ServiceDetailPage } from './ServiceDetailPage'
import { useService } from '../../../hooks/resources/useServices'
import {
  useRules,
  useCreateRule,
  useUpdateRule,
  useDeleteRule,
  useOverlapCheck,
} from '../../../hooks/resources/useRules'
import { useApplyStatus } from '../../../hooks/useApplyStatus'
import { ApiError } from '../../../api/errors'
import {
  useWhitelist,
  useAddWhitelist,
  useRemoveWhitelist,
  useBlacklist,
  useAddBlacklist,
  useRemoveBlacklist,
} from '../../../hooks/resources/useLists'

// Mock resource hooks
vi.mock('../../../hooks/resources/useServices', () => ({
  useService: vi.fn(),
}))

vi.mock('../../../hooks/resources/useRules', () => ({
  useRules: vi.fn(),
  useCreateRule: vi.fn(),
  useUpdateRule: vi.fn(),
  useDeleteRule: vi.fn(),
  useOverlapCheck: vi.fn(),
}))

vi.mock('../../../hooks/useApplyStatus', () => ({
  useApplyStatus: vi.fn(),
}))

vi.mock('../../../hooks/resources/useLists', () => ({
  useWhitelist: vi.fn(),
  useAddWhitelist: vi.fn(),
  useRemoveWhitelist: vi.fn(),
  useBlacklist: vi.fn(),
  useAddBlacklist: vi.fn(),
  useRemoveBlacklist: vi.fn(),
}))

global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))

describe('ServiceDetailPage & RulesTab & RuleForm', () => {
  const mockCreateRule = vi.fn()
  const mockUpdateRule = vi.fn()
  const mockDeleteRule = vi.fn()
  const mockOverlapCheck = vi.fn()

  const mockAddWhitelist = vi.fn()
  const mockRemoveWhitelist = vi.fn()
  const mockAddBlacklist = vi.fn()
  const mockRemoveBlacklist = vi.fn()

  const defaultWhitelistData = [
    {
      id: 'wl-1',
      service_id: 'srv-123',
      source_cidr: '192.168.1.0/24',
      created_by: 'user-1',
      created_at: '2026-07-15T00:00:00Z',
    }
  ]

  const defaultBlacklistData = [
    {
      id: 'bl-1',
      service_id: 'srv-123',
      scope: 'service' as const,
      source: 'manual' as const,
      source_cidr: '192.168.2.0/24',
      created_by: 'user-1',
      created_at: '2026-07-15T00:00:00Z',
    }
  ]

  const defaultServiceData = {
    id: 'srv-123',
    tenant_id: 'tenant-abc',
    name: 'Protected Web Server',
    cidr_or_ip: '192.0.2.0/24',
    mode: 'allow-rule-only',
    enabled: true,
    vip_pps: 50000,
    vip_bps: 100000000,
    apply_status: 'active' as const,
    version: 1,
    active_version: 1,
    plan: {
      committed_clean_gbps: 1,
      ceiling_clean_gbps: 2,
    },
  }

  const defaultRulesData = [
    {
      id: 'rule-1',
      service_id: 'srv-123',
      priority: 200,
      protocol: 'tcp' as const,
      src_port_lo: 1024,
      src_port_hi: 65535,
      dst_port_lo: 80,
      dst_port_hi: 80,
      pps: null,
      bps: null,
      enabled: true,
      warnings: [],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    },
    {
      id: 'rule-2',
      service_id: 'srv-123',
      priority: 100,
      protocol: 'udp' as const,
      src_port_lo: null,
      src_port_hi: null,
      dst_port_lo: 53,
      dst_port_hi: 53,
      pps: 1000,
      bps: 50000,
      enabled: true,
      warnings: ['Shadowed by rule-1'],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useService).mockReturnValue({
      data: defaultServiceData,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useService>)

    vi.mocked(useRules).mockReturnValue({
      data: defaultRulesData,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRules>)

    vi.mocked(useCreateRule).mockReturnValue({
      mutateAsync: mockCreateRule,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateRule>)

    vi.mocked(useUpdateRule).mockReturnValue({
      mutateAsync: mockUpdateRule,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateRule>)

    vi.mocked(useDeleteRule).mockReturnValue({
      mutateAsync: mockDeleteRule,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteRule>)

    vi.mocked(useOverlapCheck).mockReturnValue({
      mutate: mockOverlapCheck,
      data: undefined,
      isPending: false,
    } as unknown as ReturnType<typeof useOverlapCheck>)

    vi.mocked(useApplyStatus).mockReturnValue({
      data: {
        apply_status: 'active',
        version: 1,
        active_version: 1,
        last_error: null,
      },
      takingLonger: false,
    } as unknown as ReturnType<typeof useApplyStatus>)

    vi.mocked(useWhitelist).mockReturnValue({
      data: defaultWhitelistData,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useWhitelist>)

    vi.mocked(useAddWhitelist).mockReturnValue({
      mutateAsync: mockAddWhitelist,
      isPending: false,
    } as unknown as ReturnType<typeof useAddWhitelist>)

    vi.mocked(useRemoveWhitelist).mockReturnValue({
      mutateAsync: mockRemoveWhitelist,
      isPending: false,
    } as unknown as ReturnType<typeof useRemoveWhitelist>)

    vi.mocked(useBlacklist).mockReturnValue({
      data: defaultBlacklistData,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useBlacklist>)

    vi.mocked(useAddBlacklist).mockReturnValue({
      mutateAsync: mockAddBlacklist,
      isPending: false,
    } as unknown as ReturnType<typeof useAddBlacklist>)

    vi.mocked(useRemoveBlacklist).mockReturnValue({
      mutateAsync: mockRemoveBlacklist,
      isPending: false,
    } as unknown as ReturnType<typeof useRemoveBlacklist>)
  })

  afterEach(() => {
    cleanup()
  })

  const renderComponent = () => {
    return render(
      <MemoryRouter initialEntries={['/services/srv-123']}>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetailPage />} />
        </Routes>
      </MemoryRouter>
    )
  }

  const clickRulesTab = () => {
    const trigger = screen.getByRole('tab', { name: /rules/i })
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)
  }

  const clickWhitelistTab = () => {
    const trigger = screen.getByRole('tab', { name: /whitelist/i })
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)
  }

  const clickBlacklistTab = () => {
    const trigger = screen.getByRole('tab', { name: /blacklist/i })
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)
  }

  it('renders service details and active status correctly', () => {
    renderComponent()

    expect(screen.getByRole('heading', { name: 'Protected Web Server' })).toBeInTheDocument()
    expect(screen.getAllByText('192.0.2.0/24').length).toBeGreaterThan(0)
    expect(screen.getByText('50,000')).toBeInTheDocument() // PPS
    expect(screen.getByText('100.0 Mbps')).toBeInTheDocument() // BPS: 100000000 bps -> 100 Mbps
    expect(screen.getAllByText(/1\s*Gbps/).length).toBeGreaterThan(0) // Committed
    expect(screen.getAllByText(/2\s*Gbps/).length).toBeGreaterThan(0) // Ceiling
  })

  it('lists rules in ascending priority order (evaluation order visualization)', async () => {
    renderComponent()
    clickRulesTab()

    await waitFor(() => {
      expect(screen.getByText('UDP')).toBeInTheDocument()
      expect(screen.getByText('TCP')).toBeInTheDocument()
    })

    // rule-2 has priority 100 (lower number, evaluated first)
    // rule-1 has priority 200 (higher number, evaluated second)
    const rows = screen.getAllByRole('row')
    
    // Rows index: 0 = headers, 1 = rule-2 (priority 100), 2 = rule-1 (priority 200)
    expect(rows[1]).toHaveTextContent('100')
    expect(rows[1]).toHaveTextContent('#1') // Evaluation order indicator
    expect(rows[2]).toHaveTextContent('200')
    expect(rows[2]).toHaveTextContent('#2') // Evaluation order indicator
  })

  it('displays rule warnings (from rule object data) correctly in Rules list', async () => {
    renderComponent()
    clickRulesTab()

    // rule-2 contains: warnings: ['Shadowed by rule-1']
    await waitFor(() => {
      expect(screen.getByText('1 warning(s)')).toBeInTheDocument()
    })
  })

  it('enforces client-side priority uniqueness and >16 rules count limits', async () => {
    renderComponent()
    clickRulesTab()

    fireEvent.click(screen.getByRole('button', { name: /add allow rule/i }))

    const priorityInput = screen.getByLabelText(/priority/i)
    const submitBtn = screen.getByRole('button', { name: /create rule/i })

    // 1. Attempt to enter a duplicate priority (100 or 200 already exist)
    fireEvent.change(priorityInput, { target: { value: '100' } })
    fireEvent.click(submitBtn)

    expect(screen.getByText("Priority must be unique among this service's rules")).toBeInTheDocument()
    expect(mockCreateRule).not.toHaveBeenCalled()

    // 2. Now simulate having 16 rules, and check if add trigger is disabled
    const mockSixteenRules = Array.from({ length: 16 }, (_, i) => ({
      id: `rule-${i}`,
      service_id: 'srv-123',
      priority: (i + 1) * 10,
      protocol: 'tcp' as const,
      src_port_lo: null,
      src_port_hi: null,
      dst_port_lo: null,
      dst_port_hi: null,
      pps: null,
      bps: null,
      enabled: true,
      warnings: [],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    }))

    vi.mocked(useRules).mockReturnValue({
      data: mockSixteenRules,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRules>)

    cleanup()
    renderComponent()
    clickRulesTab()

    // Button should be disabled since max rules count (16) is reached
    const addBtn = screen.getByRole('button', { name: /add allow rule/i })
    expect(addBtn).toBeDisabled()
    expect(screen.getByText('Max limit of 16 rules reached')).toBeInTheDocument()
  })

  it('triggers overlap check mutation and renders warning output on matching field changes', async () => {
    // Setup mockOverlapCheck to return a warning
    vi.mocked(useOverlapCheck).mockImplementation(() => {
      return {
        mutate: vi.fn((_payload, options) => {
          if (options && options.onSuccess) {
            options.onSuccess({ warnings: ['First-match shadowing check warning: Overlaps with priority 100'] })
          }
        }),
        data: undefined,
        isPending: false,
      } as unknown as ReturnType<typeof useOverlapCheck>
    })

    renderComponent()
    clickRulesTab()

    fireEvent.click(screen.getByRole('button', { name: /add allow rule/i }))

    // Fill in matching fields to trigger useEffect overlap-check
    const protocolSelect = screen.getByLabelText(/protocol/i)
    fireEvent.change(protocolSelect, { target: { value: 'tcp' } })

    const startPortInput = screen.getAllByLabelText(/start port/i)[0] // source start port
    fireEvent.change(startPortInput, { target: { value: '80' } })

    // Wait for the debounced overlap check mutation and warnings display
    await waitFor(() => {
      expect(screen.getByText('First-match shadowing check warning: Overlaps with priority 100')).toBeInTheDocument()
    })
  })

  it('shows gateway deployment apply status and failed logs', () => {
    // Mock updating state
    vi.mocked(useApplyStatus).mockReturnValue({
      data: {
        apply_status: 'applying',
        version: 2,
        active_version: 1,
        last_error: null,
      },
      takingLonger: false,
    } as unknown as ReturnType<typeof useApplyStatus>)

    cleanup()
    renderComponent()

    expect(screen.getByText('Applying configuration changes to scrubbing gateway nodes...')).toBeInTheDocument()

    // Mock failed state
    vi.mocked(useApplyStatus).mockReturnValue({
      data: {
        apply_status: 'failed',
        version: 2,
        active_version: 1,
        last_error: 'Connection timeout with scrubbing gateway node 10.0.0.5',
      },
      takingLonger: false,
    } as unknown as ReturnType<typeof useApplyStatus>)

    cleanup()
    renderComponent()

    expect(screen.getByText(/Gateway deployment failed:/)).toBeInTheDocument()
    expect(screen.getByText(/Connection timeout with scrubbing gateway node 10.0.0.5/)).toBeInTheDocument()
  })

  it('lists whitelist and blacklist entries correctly', async () => {
    renderComponent()

    // Click whitelist tab
    clickWhitelistTab()
    await waitFor(() => {
      expect(screen.getByText('192.168.1.0/24')).toBeInTheDocument()
      expect(screen.getByText('user-1')).toBeInTheDocument()
      // VIP ceiling context
      expect(screen.getByText('50,000')).toBeInTheDocument()
      expect(screen.getByText('100.0 Mbps')).toBeInTheDocument()
    })

    // Click blacklist tab
    clickBlacklistTab()
    await waitFor(() => {
      expect(screen.getByText('192.168.2.0/24')).toBeInTheDocument()
      expect(screen.getByText('SERVICE')).toBeInTheDocument()
      expect(screen.getByText('MANUAL')).toBeInTheDocument()
    })
  })

  it('enforces client-side CIDR validation when adding entry', async () => {
    renderComponent()
    clickWhitelistTab()

    // Add entry
    fireEvent.click(screen.getByRole('button', { name: /add whitelist entry/i }))
    const input = screen.getByLabelText(/source cidr or ip/i)
    const submitBtn = screen.getByRole('button', { name: 'Add to Whitelist' })

    // Empty
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.click(submitBtn)
    expect(screen.getByText('CIDR or IP address is required')).toBeInTheDocument()
    expect(mockAddWhitelist).not.toHaveBeenCalled()

    // Invalid
    fireEvent.change(input, { target: { value: 'invalid-cidr' } })
    fireEvent.click(submitBtn)
    expect(screen.getByText('Must be a valid IP address or CIDR block')).toBeInTheDocument()
    expect(mockAddWhitelist).not.toHaveBeenCalled()

    // Valid
    fireEvent.change(input, { target: { value: '192.168.10.0/24' } })
    fireEvent.click(submitBtn)
    await waitFor(() => {
      expect(mockAddWhitelist).toHaveBeenCalledWith({ source_cidr: '192.168.10.0/24' })
    })
  })

  it('surfaces API validation error inline on 422', async () => {
    const apiValidationError = new ApiError(422, 'Validation error', [
      { loc: ['body', 'source_cidr'], msg: 'Source CIDR has overlapping definition', type: 'value_error' },
    ])
    mockAddBlacklist.mockRejectedValueOnce(apiValidationError)

    renderComponent()
    clickBlacklistTab()

    fireEvent.click(screen.getByRole('button', { name: /add blacklist entry/i }))
    const input = screen.getByLabelText(/source cidr or ip/i)
    const submitBtn = screen.getByRole('button', { name: 'Add to Blacklist' })

    fireEvent.change(input, { target: { value: '192.168.20.0/24' } })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(screen.getByText('Source CIDR has overlapping definition')).toBeInTheDocument()
    })
  })

  it('requires confirmation on delete', async () => {
    renderComponent()
    clickWhitelistTab()

    const removeBtn = screen.getAllByRole('button', { name: /remove/i })[0]
    fireEvent.click(removeBtn)

    // Verify confirm dialog is open
    expect(screen.getByText(/Are you sure you want to remove 192.168.1.0\/24 from the whitelist/i)).toBeInTheDocument()

    // Confirm
    const confirmBtn = screen.getByRole('button', { name: 'Remove' })
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockRemoveWhitelist).toHaveBeenCalledWith('192.168.1.0/24')
    })
  })
})
