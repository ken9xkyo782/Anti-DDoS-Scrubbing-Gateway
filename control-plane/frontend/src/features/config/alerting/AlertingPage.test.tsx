import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AlertingPage } from './AlertingPage'
import { useAlertRules, useUpdateAlertRule } from '../../../hooks/resources/useAlertRules'
import {
  useNotificationChannels,
  useCreateNotificationChannel,
  useUpdateNotificationChannel,
  useDeleteNotificationChannel,
  useTestNotificationChannel,
} from '../../../hooks/resources/useNotificationChannels'

vi.mock('../../../hooks/resources/useAlertRules', () => ({
  useAlertRules: vi.fn(),
  useUpdateAlertRule: vi.fn(),
}))

vi.mock('../../../hooks/resources/useNotificationChannels', () => ({
  useNotificationChannels: vi.fn(),
  useCreateNotificationChannel: vi.fn(),
  useUpdateNotificationChannel: vi.fn(),
  useDeleteNotificationChannel: vi.fn(),
  useTestNotificationChannel: vi.fn(),
}))

global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
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

describe('AlertingPage & Components', () => {
  const mockUpdateAlertRule = vi.fn()
  const mockCreateChannel = vi.fn()
  const mockUpdateChannel = vi.fn()
  const mockDeleteChannel = vi.fn()
  const mockTestChannel = vi.fn()

  const defaultRulesData = [
    {
      key: 'high-cpu',
      enabled: true,
      severity: 'warning' as const,
      fire_threshold: 85.0,
      clear_threshold: 75.0,
      silence_in_maintenance: true,
    },
    {
      key: 'service-down',
      enabled: false,
      severity: 'critical' as const,
      fire_threshold: 1.0,
      clear_threshold: 0.0,
      silence_in_maintenance: false,
    },
  ]

  const defaultChannelsData = [
    {
      id: 'channel-1',
      name: 'Slack Alerts',
      kind: 'webhook' as const,
      tenant_id: null,
      enabled: true,
      min_severity: 'warning' as const,
      config: { url: 'https://hooks.slack.com/services/abc' },
    },
    {
      id: 'channel-2',
      name: 'Ops Email',
      kind: 'email' as const,
      tenant_id: null,
      enabled: false,
      min_severity: 'critical' as const,
      config: {
        smtp_host: 'smtp.ops.com',
        from: 'alerts@ops.com',
        to: ['oncall@ops.com', 'backup@ops.com'],
      },
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useAlertRules).mockReturnValue({
      data: defaultRulesData,
      isLoading: false,
      isError: false,
      error: null,
    } as never)

    vi.mocked(useUpdateAlertRule).mockReturnValue({
      mutateAsync: mockUpdateAlertRule,
      isPending: false,
    } as never)

    vi.mocked(useNotificationChannels).mockReturnValue({
      data: defaultChannelsData,
      isLoading: false,
      isError: false,
      error: null,
    } as never)

    vi.mocked(useCreateNotificationChannel).mockReturnValue({
      mutateAsync: mockCreateChannel,
      isPending: false,
    } as never)

    vi.mocked(useUpdateNotificationChannel).mockReturnValue({
      mutateAsync: mockUpdateChannel,
      isPending: false,
    } as never)

    vi.mocked(useDeleteNotificationChannel).mockReturnValue({
      mutateAsync: mockDeleteChannel,
      isPending: false,
    } as never)

    vi.mocked(useTestNotificationChannel).mockReturnValue({
      mutateAsync: mockTestChannel,
      isPending: false,
    } as never)
  })

  afterEach(() => {
    cleanup()
  })

  it('renders alerting page tabs and active list items', () => {
    render(<AlertingPage />)

    // Verify page header
    expect(screen.getByText('Alerting Configuration')).toBeInTheDocument()

    // Rules tab list
    expect(screen.getByText('high-cpu')).toBeInTheDocument()
    expect(screen.getByText('service-down')).toBeInTheDocument()

    // Go to Channels tab
    const trigger = screen.getByRole('tab', { name: /notification channels/i })
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)

    expect(screen.getByText('Slack Alerts')).toBeInTheDocument()
    expect(screen.getByText('Ops Email')).toBeInTheDocument()
  })

  it('handles alert rule threshold patch', async () => {
    render(<AlertingPage />)

    // Click edit button for high-cpu
    const editBtns = screen.getAllByRole('button', { name: 'Edit' })
    fireEvent.click(editBtns[0])

    expect(screen.getByText('Configure Alert Rule: high-cpu')).toBeInTheDocument()

    // Change thresholds
    fireEvent.change(screen.getByLabelText(/fire threshold/i), { target: { value: '90' } })
    fireEvent.change(screen.getByLabelText(/clear threshold/i), { target: { value: '80' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdateAlertRule).toHaveBeenCalledWith({
        enabled: true,
        severity: 'warning',
        fire_threshold: 90,
        clear_threshold: 80,
        silence_in_maintenance: true,
      })
    })
  })

  it('handles channel creation with kind SMTP validation', async () => {
    render(<AlertingPage />)
    const trigger = screen.getByRole('tab', { name: /notification channels/i })
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('button', { name: /add channel/i }))

    // Default kind is webhook (or let's select Email)
    fireEvent.change(screen.getByLabelText(/channel kind/i), { target: { value: 'email' } })

    const saveBtn = screen.getByRole('button', { name: 'Save' })
    fireEvent.click(saveBtn)

    // Should display validation errors
    expect(screen.getByText('Channel name is required')).toBeInTheDocument()
    expect(screen.getByText('SMTP Host is required')).toBeInTheDocument()
    expect(screen.getByText('Sender address is required')).toBeInTheDocument()
    expect(screen.getByText('At least one recipient is required')).toBeInTheDocument()

    // Fill details
    fireEvent.change(screen.getByLabelText(/channel name/i), { target: { value: 'My SMTP' } })
    fireEvent.change(screen.getByLabelText(/smtp host/i), { target: { value: 'smtp.mail.com' } })
    fireEvent.change(screen.getByLabelText(/sender/i), { target: { value: 'me@mail.com' } })
    fireEvent.change(screen.getByLabelText(/recipients/i), { target: { value: 'them@mail.com' } })

    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockCreateChannel).toHaveBeenCalledWith({
        name: 'My SMTP',
        kind: 'email',
        enabled: true,
        min_severity: 'info',
        config: {
          smtp_host: 'smtp.mail.com',
          from: 'me@mail.com',
          to: ['them@mail.com'],
        },
      })
    })
  })

  it('handles channel secret write-only logic during edit', async () => {
    render(<AlertingPage />)
    const trigger = screen.getByRole('tab', { name: /notification channels/i })
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)

    // Edit Slack Alerts
    const editBtns = screen.getAllByRole('button', { name: 'Edit' })
    fireEvent.click(editBtns[0])

    const secretInput = screen.getByLabelText(/secret/i) as HTMLInputElement
    expect(secretInput.value).toBe('')

    const saveBtn = screen.getByRole('button', { name: 'Save' })
    fireEvent.click(saveBtn)

    // Verify secret is NOT sent if not typed in
    await waitFor(() => {
      expect(mockUpdateChannel).toHaveBeenCalledWith({
        name: 'Slack Alerts',
        kind: 'webhook',
        enabled: true,
        min_severity: 'warning',
        config: { url: 'https://hooks.slack.com/services/abc' },
      })
    })

    // Now type a secret
    fireEvent.click(editBtns[0])
    fireEvent.change(screen.getByLabelText(/secret/i), { target: { value: 'my-new-secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockUpdateChannel).toHaveBeenCalledWith({
        name: 'Slack Alerts',
        kind: 'webhook',
        enabled: true,
        min_severity: 'warning',
        config: { url: 'https://hooks.slack.com/services/abc' },
        secret: 'my-new-secret',
      })
    })
  })

  it('handles channel test-send and displays result', async () => {
    mockTestChannel.mockResolvedValue({
      state: 'success',
      attempts: 1,
      error: null,
    })

    render(<AlertingPage />)
    const trigger = screen.getByRole('tab', { name: /notification channels/i })
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)

    const testBtns = screen.getAllByRole('button', { name: 'Test' })
    fireEvent.click(testBtns[0])

    expect(screen.getByText('Sending test notification...')).toBeInTheDocument()

    await waitFor(() => {
      expect(mockTestChannel).toHaveBeenCalled()
      expect(screen.getByText('Test notification sent successfully!')).toBeInTheDocument()
    })
  })
})
