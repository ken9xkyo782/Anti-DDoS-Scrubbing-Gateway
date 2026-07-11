import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AuthContext, type AuthContextValue, type Principal } from '../auth/AuthContext'
import { ProtectedRoute } from './ProtectedRoute'

const admin: Principal = {
  id: 'a4f3df34-a15b-4482-9e16-5b5604c7ae9d',
  username: 'admin',
  role: 'admin',
  tenant_id: null,
}

function renderRoute(principal: Principal | null) {
  const value: AuthContextValue = {
    principal,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }

  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={['/admin']}>
        <Routes>
          <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
            <Route path="/admin" element={<p>Admin dashboard</p>} />
          </Route>
          <Route path="/login" element={<p>Login page</p>} />
          <Route path="/forbidden" element={<p>Access denied</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('ProtectedRoute', () => {
  it('allows an authenticated permitted role', () => {
    renderRoute(admin)
    expect(screen.getByText('Admin dashboard')).toBeInTheDocument()
  })

  it('redirects an anonymous user to login', () => {
    renderRoute(null)
    expect(screen.getByText('Login page')).toBeInTheDocument()
  })

  it('redirects an authenticated forbidden role', () => {
    renderRoute(
      { ...admin, role: 'tenant_user', tenant_id: 'e3e80c9b-09e3-4172-a4ba-c59f3233411f' },
    )
    expect(screen.getByText('Access denied')).toBeInTheDocument()
  })
})
