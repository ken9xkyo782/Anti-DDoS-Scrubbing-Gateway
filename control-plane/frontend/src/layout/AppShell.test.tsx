import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { AuthContext, type AuthContextValue, type Principal } from '../auth/AuthContext'
import { App } from '../App'

const mockAdminPrincipal: Principal = {
  id: 'admin-id',
  username: 'admin_user',
  role: 'admin',
  tenant_id: null,
}

const mockTenantPrincipal: Principal = {
  id: 'tenant-id',
  username: 'tenant_user',
  role: 'tenant_user',
  tenant_id: 'tenant-123',
}

function renderWithProviders(
  principal: Principal | null,
  initialEntries = ['/'],
  isLoading = false
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  const logoutMock = vi.fn().mockResolvedValue(undefined)
  const loginMock = vi.fn().mockResolvedValue(undefined)

  const authValue: AuthContextValue = {
    principal,
    isLoading,
    login: loginMock,
    logout: logoutMock,
  }

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={authValue}>
        <MemoryRouter initialEntries={initialEntries}>
          <App />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>
  )

  return {
    ...utils,
    logoutMock,
    loginMock,
  }
}

describe('AppShell - Sidebar + Topbar + role-aware routing', () => {
  beforeEach(() => {
    // Clean up document attributes
    document.documentElement.removeAttribute('data-theme')
  })

  afterEach(() => {
    cleanup()
  })

  it('renders role-filtered nav for tenant users', () => {
    renderWithProviders(mockTenantPrincipal, ['/tenant'])

    // Tenant user sees dashboard, services and allocations
    expect(screen.getAllByText('Dashboard')[0]).toBeInTheDocument()
    expect(screen.getByText('My Services')).toBeInTheDocument()
    expect(screen.getByText('Allocations')).toBeInTheDocument()

    // Tenant user DOES NOT see admin links
    expect(screen.queryByText('Tenants')).not.toBeInTheDocument()
    expect(screen.queryByText('Users')).not.toBeInTheDocument()
    expect(screen.queryByText('Threat Feeds')).not.toBeInTheDocument()
  })

  it('renders role-filtered nav for admin users', () => {
    renderWithProviders(mockAdminPrincipal, ['/admin'])

    // Admin sees admin dashboard, services oversight, tenants, users, etc.
    expect(screen.getAllByText('Dashboard')[0]).toBeInTheDocument()
    expect(screen.getByText('Services')).toBeInTheDocument()
    expect(screen.getByText('Tenants')).toBeInTheDocument()
    expect(screen.getByText('Users')).toBeInTheDocument()
    expect(screen.getByText('Node Control')).toBeInTheDocument()

    // Admin DOES NOT see tenant-only "My Services" (tenant menu item uses "My Services")
    expect(screen.queryByText('My Services')).not.toBeInTheDocument()
  })

  it('blocks tenant users from admin-only routes', async () => {
    renderWithProviders(mockTenantPrincipal, ['/admin/tenants'])

    // Verify redirection to /forbidden or display of Forbidden text
    expect(screen.getByText('Access denied')).toBeInTheDocument()
    expect(screen.queryByText('Tenants Management')).not.toBeInTheDocument()
  })

  it('redirects anonymous users to login page', () => {
    renderWithProviders(null, ['/tenant'])

    // ProtectedRoute redirects to /login when principal is null
    // Since LoginPage has a heading "Anti-DDoS Control Plane" and a submit button "Sign in"
    expect(screen.getByRole('heading', { name: /anti-ddos control plane/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('handles theme toggling and persists to localStorage', () => {
    const { getByLabelText } = renderWithProviders(mockTenantPrincipal, ['/tenant'])
    
    const themeBtn = getByLabelText(/switch to dark theme/i)
    expect(themeBtn).toBeInTheDocument()
    
    // Default system is light, so toggle to dark
    fireEvent.click(themeBtn)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem('theme')).toBe('dark')

    // Toggle back to light
    const themeBtn2 = getByLabelText(/switch to light theme/i)
    fireEvent.click(themeBtn2)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(localStorage.getItem('theme')).toBe('light')
  })
})
