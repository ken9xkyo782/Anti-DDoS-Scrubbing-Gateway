import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { NodeControlPage } from './NodeControlPage'
import { useNodeControl } from '../../../hooks/resources/useNodeControl'

vi.mock('../../../hooks/resources/useNodeControl', () => ({
  useNodeControl: vi.fn(),
}))

vi.mock('../../../ui/Toast/Toast', () => ({
  toast: vi.fn(),
}))

global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))

describe('NodeControlPage', () => {
  const mockBypassMutate = vi.fn()
  const mockMaintMutate = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useNodeControl).mockReturnValue({
      healthQuery: {
        data: {
          has_data: true,
          xdp_mode: 'native',
          active_slot: 1,
          map_version: 42,
          map_error_count: 0,
          node_clean_bps: 1000,
          node_capacity_bps: 100000,
          window_start: '2026-07-15T08:00:00Z',
          window_seconds: 10,
          stale: false,
          job_backlog: { queued: 0, applying: 0 },
          feed_sources: [],
          bypass: {
            desired: false,
            effective: false,
            activated_at: null,
            active_seconds: 0,
          },
          maintenance: {
            desired: false,
            effective: false,
            activated_at: null,
            active_seconds: 0,
          },
        },
        isLoading: false,
        error: null,
      } as unknown as ReturnType<typeof useNodeControl>['healthQuery'],
      bypassMutation: {
        mutateAsync: mockBypassMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useNodeControl>['bypassMutation'],
      maintenanceMutation: {
        mutateAsync: mockMaintMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useNodeControl>['maintenanceMutation'],
    })
  })

  afterEach(() => {
    cleanup()
  })

  it('renders current desired and effective status correctly', () => {
    render(<NodeControlPage />)

    expect(screen.getByText('Node Control')).toBeDefined()
    expect(screen.getByText('Global Scrubbing Bypass')).toBeDefined()
    expect(screen.getByText('Maintenance Mode')).toBeDefined()

    // Status cards should indicate inactive
    expect(screen.getAllByText('Inactive')).toHaveLength(4) // Bypass Desired, Bypass Effective, Maintenance Desired, Maintenance Effective
  })

  it('toggling bypass on requires confirmation, handles reason input, and calls mutation', async () => {
    render(<NodeControlPage />)

    // The bypass switch
    const bypassSwitch = screen.getByLabelText('Toggle Scrubbing Bypass')
    expect(bypassSwitch).toBeDefined()
    
    // Toggle bypass on
    fireEvent.click(bypassSwitch)

    // Verify it explains that bypass disables scrubbing
    expect(screen.getByText('Enable Global Scrubbing Bypass?')).toBeDefined()

    // Fill in a reason
    const reasonInput = screen.getByPlaceholderText(/reason/i)
    fireEvent.change(reasonInput, { target: { value: 'Scheduled hardware swap' } })

    // Find and click confirm button
    const confirmButton = screen.getByRole('button', { name: /confirm bypass/i })
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(mockBypassMutate).toHaveBeenCalledWith({ enabled: true, reason: 'Scheduled hardware swap' })
    })
  })

  it('toggling bypass off calls mutation directly or via confirm', async () => {
    // Mock health query with active bypass
    vi.mocked(useNodeControl).mockReturnValue({
      healthQuery: {
        data: {
          bypass: {
            desired: true,
            effective: true,
            activated_at: '2026-07-15T08:00:00Z',
            active_seconds: 60,
          },
          maintenance: {
            desired: false,
            effective: false,
            activated_at: null,
            active_seconds: 0,
          },
        },
        isLoading: false,
        error: null,
      } as unknown as ReturnType<typeof useNodeControl>['healthQuery'],
      bypassMutation: {
        mutateAsync: mockBypassMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useNodeControl>['bypassMutation'],
      maintenanceMutation: {
        mutateAsync: mockMaintMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useNodeControl>['maintenanceMutation'],
    })

    render(<NodeControlPage />)

    const bypassSwitch = screen.getByLabelText('Toggle Scrubbing Bypass')
    
    // Toggle bypass off
    fireEvent.click(bypassSwitch)

    // Check confirmation dialog appears
    expect(screen.getByText(/disable node bypass/i)).toBeDefined()
    const confirmButton = screen.getByRole('button', { name: /confirm/i })
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(mockBypassMutate).toHaveBeenCalledWith({ enabled: false })
    })
  })

  it('toggling maintenance mode on explains queue-and-apply behavior and calls mutation', async () => {
    render(<NodeControlPage />)

    const maintSwitch = screen.getByLabelText('Toggle Maintenance Mode')
    expect(maintSwitch).toBeDefined()

    // Toggle maintenance on
    fireEvent.click(maintSwitch)

    // Verify it explains queue-and-apply-on-exit
    expect(screen.getByText('Enable Maintenance Mode?')).toBeDefined()

    const confirmButton = screen.getByRole('button', { name: /confirm/i })
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(mockMaintMutate).toHaveBeenCalledWith({ enabled: true })
    })
  })
})
