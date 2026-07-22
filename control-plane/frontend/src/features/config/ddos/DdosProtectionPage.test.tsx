import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { DdosProtectionPage } from './DdosProtectionPage'
import {
  useAmplificationConfig,
  useAddBlockedPort,
  useRemoveBlockedPort,
} from '../../../hooks/resources/useAmplificationConfig'
import { ApiError } from '../../../api/client'

vi.mock('../../../hooks/resources/useAmplificationConfig', () => ({
  useAmplificationConfig: vi.fn(),
  useAddBlockedPort: vi.fn(),
  useRemoveBlockedPort: vi.fn(),
}))

describe('DdosProtectionPage & BlockedPortForm', () => {
  const mockAddBlockedPort = vi.fn()
  const mockRemoveBlockedPort = vi.fn()

  const defaultAmplificationConfig = {
    hardcoded_ports: [17, 19, 53, 111, 123, 137, 161, 389, 520, 1900, 5353, 11211],
    dynamic_ports: [
      {
        port: 9999,
        note: 'Game server amplification',
        created_by: 'user-1',
        created_at: '2026-07-22T00:00:00Z',
      },
    ],
  }

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useAmplificationConfig).mockReturnValue({
      data: defaultAmplificationConfig,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useAmplificationConfig>)

    vi.mocked(useAddBlockedPort).mockReturnValue({
      mutateAsync: mockAddBlockedPort,
      isPending: false,
    } as unknown as ReturnType<typeof useAddBlockedPort>)

    vi.mocked(useRemoveBlockedPort).mockReturnValue({
      mutateAsync: mockRemoveBlockedPort,
      isPending: false,
    } as unknown as ReturnType<typeof useRemoveBlockedPort>)
  })

  afterEach(() => {
    cleanup()
  })

  it('renders built-in hardcoded ports and dynamic ports table correctly', () => {
    render(
      <MemoryRouter>
        <DdosProtectionPage />
      </MemoryRouter>
    )

    expect(screen.getByText('DDoS Protection')).toBeInTheDocument()
    expect(screen.getByText('Built-in blocked source ports (always on)')).toBeInTheDocument()
    expect(screen.getByText('UDP/53')).toBeInTheDocument()
    expect(screen.getByText('UDP/1900')).toBeInTheDocument()

    expect(screen.getByText('UDP/9999')).toBeInTheDocument()
    expect(screen.getByText('Game server amplification')).toBeInTheDocument()
  })

  it('handles add blocked port success flow', async () => {
    mockAddBlockedPort.mockResolvedValueOnce({
      port: 8888,
      note: 'SSDP',
      created_by: 'admin',
      created_at: '2026-07-22T00:00:00Z',
    })

    render(
      <MemoryRouter>
        <DdosProtectionPage />
      </MemoryRouter>
    )

    const addButton = screen.getByRole('button', { name: /Add Blocked Port/i })
    fireEvent.click(addButton)

    expect(screen.getByText('Add Blocked UDP Port')).toBeInTheDocument()

    const portInput = screen.getByLabelText(/Port Number/i)
    fireEvent.change(portInput, { target: { value: '8888' } })

    const noteInput = screen.getByLabelText(/Note \/ Reason/i)
    fireEvent.change(noteInput, { target: { value: 'SSDP' } })

    const submitButton = screen.getByRole('button', { name: /^Block Port$/i })
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(mockAddBlockedPort).toHaveBeenCalledWith({
        port: 8888,
        note: 'SSDP',
      })
    })
  })

  it('surfaces 409 conflict error inline when port is already blocked', async () => {
    mockAddBlockedPort.mockRejectedValueOnce(
      new ApiError(409, 'port already blocked', 'port already blocked')
    )

    render(
      <MemoryRouter>
        <DdosProtectionPage />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole('button', { name: /Add Blocked Port/i }))

    const portInput = screen.getByLabelText(/Port Number/i)
    fireEvent.change(portInput, { target: { value: '9999' } })

    fireEvent.click(screen.getByRole('button', { name: /^Block Port$/i }))

    await waitFor(() => {
      expect(screen.getByText('port already blocked')).toBeInTheDocument()
    })
  })

  it('handles remove blocked port confirmation flow', async () => {
    mockRemoveBlockedPort.mockResolvedValueOnce(undefined)

    render(
      <MemoryRouter>
        <DdosProtectionPage />
      </MemoryRouter>
    )

    const removeButton = screen.getByRole('button', { name: /Remove/i })
    fireEvent.click(removeButton)

    expect(screen.getByText(/Are you sure you want to unblock UDP port 9999/i)).toBeInTheDocument()

    const confirmButton = screen.getByRole('button', { name: /^Remove$/i })
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(mockRemoveBlockedPort).toHaveBeenCalledWith(9999)
    })
  })
})
