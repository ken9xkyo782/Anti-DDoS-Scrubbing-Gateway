import { Navigate, Route, Routes } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import { AppShell } from './layout/AppShell'
import { AdminDashboard } from './pages/AdminDashboard'
import { BillingPanel } from './components/BillingPanel'
import { AlertsPanel } from './components/AlertsPanel'
import { LoginPage } from './pages/LoginPage'
import { TenantDashboard } from './pages/TenantDashboard'
import { ProtectedRoute } from './routes/ProtectedRoute'
import { ServicesPage } from './features/config/services/ServicesPage'

function DashboardLanding() {
  const { principal } = useAuth()

  return <Navigate to={principal?.role === 'admin' ? '/admin' : '/tenant'} replace />
}

function ForbiddenPage() {
  return <main style={{ padding: 'var(--space-6)' }}><h1>Access denied</h1></main>
}

function ComingSoon({ title }: { title: string }) {
  return (
    <div>
      <h1>{title}</h1>
      <p style={{ color: 'var(--text-muted)', marginTop: 'var(--space-2)' }}>This feature is coming soon.</p>
    </div>
  )
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forbidden" element={<ForbiddenPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<DashboardLanding />} />
          <Route path="/billing" element={<BillingPanel />} />
          <Route path="/alerts" element={<AlertsPanel />} />
          <Route path="/account" element={<ComingSoon title="Account Settings" />} />
          
          {/* Tenant-only routes */}
          <Route element={<ProtectedRoute allowedRoles={['tenant_user']} />}>
            <Route path="/tenant" element={<TenantDashboard />} />
            <Route path="/services" element={<ServicesPage />} />
            <Route path="/allocations" element={<ComingSoon title="My Allocations" />} />
          </Route>

          {/* Admin-only routes */}
          <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/admin/services" element={<ComingSoon title="Services Oversight" />} />
            <Route path="/admin/tenants" element={<ComingSoon title="Tenants Management" />} />
            <Route path="/admin/users" element={<ComingSoon title="Users Management" />} />
            <Route path="/admin/allocations" element={<ComingSoon title="CIDR Allocations" />} />
            <Route path="/admin/feeds" element={<ComingSoon title="Threat Feeds" />} />
            <Route path="/admin/global-blacklist" element={<ComingSoon title="Global Blacklist" />} />
            <Route path="/admin/alerting" element={<ComingSoon title="Alerting Configuration" />} />
            <Route path="/admin/node" element={<ComingSoon title="Node Control" />} />
            <Route path="/admin/jobs" element={<ComingSoon title="Job Backlog" />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

