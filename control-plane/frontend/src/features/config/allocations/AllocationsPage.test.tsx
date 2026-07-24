import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AllocationsPage } from './AllocationsPage'
import { useAuth } from '../../../auth/AuthContext'
import { useTenants } from '../../../hooks/resources/useTenants'
import {
  useAllocations,
  useMyAllocations,
  useCreateAllocation,
  useRevokeAllocation,
  useCheckOverlap,
} from '../../../hooks/resources/useAllocations'
import { ApiError } from '../../../api/errors'

vi.mock('../../../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}))

vi.mock('../../../hooks/resources/useTenants', () => ({
  useTenants: vi.fn(),
}))

vi.mock('../../../hooks/resources/useAllocations', () => ({
  useAllocations: vi.fn(),
  useMyAllocations: vi.fn(),
  useCreateAllocation: vi.fn(),
  useRevokeAllocation: vi.fn(),
  useCheckOverlap: vi.fn(),
}))

describe('AllocationsPage & AllocationForm', () => {
  const mockCreateAllocation = vi.fn()
  const mockRevokeAllocation = vi.fn()
  const mockCheckOverlap = vi.fn()

  const defaultTenants = [
    { id: 'tenant-1', name: 'Tenant One', status: 'active' },
    { id: 'tenant-2', name: 'Tenant Two', status: 'active' },
  ]

  const mockAdminPrincipal = {
    id: 'admin-id',
    username: 'admin',
    role: 'admin',
    tenant_id: null,
  }

  const mockTenantPrincipal = {
    id: 'tenant-user-id',
    username: 'user1',
    role: 'tenant_user',
    tenant_id: 'tenant-1',
  }

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useTenants).mockReturnValue({
      data: defaultTenants,
      isLoading: false,
      error: null,
    } as never)

    vi.mocked(useCreateAllocation).mockReturnValue({
      mutateAsync: mockCreateAllocation,
      isPending: false,
    } as never)

    vi.mocked(useRevokeAllocation).mockReturnValue({
      mutateAsync: mockRevokeAllocation,
      isPending: false,
    } as never)

    vi.mocked(useCheckOverlap).mockReturnValue({
      mutateAsync: mockCheckOverlap,
      isPending: false,
    } as never)
  })

  afterEach(() => {
    cleanup()
  })

  const renderComponent = () => {
    return render(
      <MemoryRouter>
        <AllocationsPage />
      </MemoryRouter>
    )
  }

  describe('Tenant User View', () => {
    beforeEach(() => {
      vi.mocked(useAuth).mockReturnValue({
        principal: mockTenantPrincipal,
        isLoading: false,
      } as never)

      vi.mocked(useMyAllocations).mockReturnValue({
        data: [
          {
            id: 'alloc-1',
            tenant_id: 'tenant-1',
            cidr: '203.0.113.0/24',
            status: 'active',
            allocated_by: 'admin-id',
            created_at: '2026-07-15T00:00:00Z',
            updated_at: '2026-07-15T00:00:00Z',
          },
        ],
        isLoading: false,
        error: null,
      } as never)
    })

    it('renders read-only tenant allocations list', () => {
      renderComponent()
      expect(screen.getByText('My Allocations')).toBeInTheDocument()
      expect(screen.getByText('203.0.113.0/24')).toBeInTheDocument()
      expect(screen.getByText('Active')).toBeInTheDocument()
      expect(screen.queryByTestId('allocate-btn')).not.toBeInTheDocument()
      expect(screen.queryByText('Revoke')).not.toBeInTheDocument()
    })
  })

  describe('Admin View', () => {
    beforeEach(() => {
      vi.mocked(useAuth).mockReturnValue({
        principal: mockAdminPrincipal,
        isLoading: false,
      } as never)

      vi.mocked(useAllocations).mockReturnValue({
        data: [
          {
            allocation: {
              id: 'alloc-1',
              tenant_id: 'tenant-1',
              cidr: '203.0.113.0/24',
              status: 'active',
              allocated_by: 'admin-id',
              created_at: '2026-07-15T00:00:00Z',
              updated_at: '2026-07-15T00:00:00Z',
            },
            dependent_count: 2,
          },
        ],
        isLoading: false,
        error: null,
      } as never)
    })

    it('renders admin allocations list with actions and tenant selector', () => {
      renderComponent()
      expect(screen.getByText('CIDR Allocations')).toBeInTheDocument()
      expect(screen.getByText('Active Tenant:')).toBeInTheDocument()
      expect(screen.getByText('203.0.113.0/24')).toBeInTheDocument()
      expect(screen.getByText('2')).toBeInTheDocument() // Dependent count
      expect(screen.getByTestId('revoke-btn-203.0.113.0/24')).toBeInTheDocument()
    })

    it('handles revoking an allocation with confirm and success flow', async () => {
      mockRevokeAllocation.mockResolvedValueOnce({})
      renderComponent()

      fireEvent.click(screen.getByTestId('revoke-btn-203.0.113.0/24'))
      expect(screen.getByText('Revoke CIDR Allocation')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))
      await waitFor(() => {
        expect(mockRevokeAllocation).toHaveBeenCalled()
      })
    })

    it('surfaces revoke-in-use blocker warning when revoke fails due to dependencies', async () => {
      const apiError = new ApiError(409, 'Conflict', {
        message: 'Allocation is still in use',
        blockers: ['protected_service:web-service'],
      } as never)
      mockRevokeAllocation.mockRejectedValueOnce(apiError)

      renderComponent()
      fireEvent.click(screen.getByTestId('revoke-btn-203.0.113.0/24'))
      fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))

      await waitFor(() => {
        expect(screen.queryByText('Revoke CIDR Allocation')).not.toBeInTheDocument()
      })
    })

    it('opens allocation form, performs overlap checking on submit, and handles success allocation creation', async () => {
      mockCheckOverlap.mockResolvedValue({ overlaps: false, conflicts: [] })
      mockCreateAllocation.mockResolvedValue({})

      renderComponent()
      fireEvent.click(screen.getByTestId('allocate-btn'))

      expect(screen.getByText('Allocate CIDR to Tenant One')).toBeInTheDocument()

      const input = screen.getByTestId('cidr-input')
      fireEvent.change(input, { target: { value: '198.51.100.0/24' } })

      expect(mockCheckOverlap).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: 'Allocate' }))

      await waitFor(() => {
        expect(mockCheckOverlap).toHaveBeenCalledWith({ cidr: '198.51.100.0/24' })
        expect(mockCreateAllocation).toHaveBeenCalledWith({
          tenant_id: 'tenant-1',
          cidr: '198.51.100.0/24',
        })
      })
    })

    it('shows error when overlap checking reports conflicts on submit', async () => {
      mockCheckOverlap.mockResolvedValue({
        overlaps: true,
        conflicts: [
          { cidr: '198.51.100.0/24', tenant_id: 'tenant-2' },
        ],
      })

      renderComponent()
      fireEvent.click(screen.getByTestId('allocate-btn'))

      const input = screen.getByTestId('cidr-input')
      fireEvent.change(input, { target: { value: '198.51.100.128/25' } })

      fireEvent.click(screen.getByRole('button', { name: 'Allocate' }))

      await waitFor(() => {
        expect(screen.getByTestId('submit-error')).toHaveTextContent(
          'Failed: CIDR overlaps with existing allocations (198.51.100.0/24)'
        )
      })
    })
  })
})
