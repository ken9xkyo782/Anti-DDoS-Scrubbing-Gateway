import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthContext, type AuthContextValue, type Role } from '../auth/AuthContext'
import { BillingPanel } from './BillingPanel'

const { useBillingUsage } = vi.hoisted(() => ({ useBillingUsage: vi.fn() }))

vi.mock('../hooks/useBillingUsage', () => ({
  useBillingUsage: (...args: unknown[]) => useBillingUsage(...args),
}))

afterEach(cleanup)

const currentUsage = {
  service_id: 'service-1',
  service_name: 'Payments API',
  tenant_id: 'tenant-1',
  period_start: '2026-07-01T00:00:00Z',
  period_end: '2026-08-01T00:00:00Z',
  billing_metric: 'p95_clean_bps',
  committed_clean_gbps: '10.00',
  p95_clean_gbps: '12.50',
  billed_gbps: '12.50',
  overage_gbps: '2.50',
  overage_policy: 'billed',
  sample_count: 42,
  status: 'open' as const,
  provisional: true,
}

const finalizedUsage = {
  ...currentUsage,
  period_start: '2026-06-01T00:00:00Z',
  period_end: '2026-07-01T00:00:00Z',
  p95_clean_gbps: '8.00',
  billed_gbps: '10.00',
  overage_gbps: '0.00',
  sample_count: 8640,
  status: 'final' as const,
  provisional: false,
}

function renderPanel(role: Role) {
  const value: AuthContextValue = {
    principal: {
      id: 'user-1',
      username: 'billing-user',
      role,
      tenant_id: role === 'admin' ? null : 'tenant-1',
    },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }

  return render(
    <AuthContext.Provider value={value}>
      <BillingPanel />
    </AuthContext.Provider>,
  )
}

describe('BillingPanel', () => {
  it('shows a loading state while usage is requested', () => {
    useBillingUsage.mockReturnValue({ isPending: true, isError: false })

    renderPanel('tenant_user')

    expect(screen.getByText('Loading billing usage…')).toBeInTheDocument()
  })

  it('shows an empty state when the usage API has no records', () => {
    useBillingUsage.mockReturnValue({
      data: { usage: [], has_data: false },
      isPending: false,
      isError: false,
    })

    renderPanel('tenant_user')

    expect(screen.getByRole('heading', { name: 'Billing' })).toBeInTheDocument()
    expect(screen.getByText('No billing usage is available yet.')).toBeInTheDocument()
  })

  it('shows a tenant each current service alongside finalized periods', () => {
    useBillingUsage.mockReturnValue({
      data: { usage: [currentUsage, finalizedUsage], has_data: true },
      isPending: false,
      isError: false,
    })

    renderPanel('tenant_user')

    expect(screen.getByRole('heading', { name: 'Current service usage' })).toBeInTheDocument()
    const currentServiceUsage = screen.getByRole('region', { name: 'Current service usage' })
    expect(within(currentServiceUsage).getByText('Payments API')).toBeInTheDocument()
    expect(within(currentServiceUsage).getByText('12.50 Gbps')).toBeInTheDocument()
    expect(within(currentServiceUsage).getByText('10.00 Gbps')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Finalized periods' })).toBeInTheDocument()
    expect(screen.getByText('2026-06')).toBeInTheDocument()
  })

  it('shows admin node totals and marks overage and open rows as provisional', () => {
    useBillingUsage.mockReturnValue({
      data: {
        usage: [
          currentUsage,
          {
            ...currentUsage,
            service_id: 'service-2',
            service_name: 'Customer Portal',
            tenant_id: 'tenant-2',
            committed_clean_gbps: '10.00',
            billed_gbps: '14.00',
            overage_gbps: '4.00',
          },
        ],
        has_data: true,
      },
      isPending: false,
      isError: false,
    })

    renderPanel('admin')

    expect(screen.getByRole('heading', { name: 'Node-wide current usage' })).toBeInTheDocument()
    expect(screen.getByText('26.50 Gbps')).toBeInTheDocument()
    expect(screen.getByText('20.00 Gbps')).toBeInTheDocument()
    expect(screen.getAllByText('Provisional')).toHaveLength(2)
    const currentServiceUsage = screen.getByRole('region', { name: 'Current service usage' })
    expect(within(currentServiceUsage).getByText('Overage: 2.50 Gbps')).toBeInTheDocument()
    expect(within(currentServiceUsage).getByText('Overage: 4.00 Gbps')).toBeInTheDocument()
  })
})
