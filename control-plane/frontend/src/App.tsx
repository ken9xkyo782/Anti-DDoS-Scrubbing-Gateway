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
import { ServiceDetailPage } from './features/config/services/ServiceDetailPage'
import { TenantsPage } from './features/config/tenants/TenantsPage'
import { UsersPage } from './features/config/users/UsersPage'
import { AllocationsPage } from './features/config/allocations/AllocationsPage'
import { AdminServicesPage } from './features/config/services-admin/AdminServicesPage'
import { FeedsPage } from './features/config/feeds/FeedsPage'
import { GlobalBlacklistPage } from './features/config/global-blacklist/GlobalBlacklistPage'
import { DdosProtectionPage } from './features/config/ddos/DdosProtectionPage'
import { DdosCoveragePage } from './features/config/ddos/DdosCoveragePage'
import { AlertingPage } from './features/config/alerting/AlertingPage'
import { NodeControlPage } from './features/config/node/NodeControlPage'
import { AccountPage } from './features/config/account/AccountPage'
import { JobBacklogPage } from './features/config/jobs/JobBacklogPage'

function DashboardLanding() {
  const { principal } = useAuth()

  return <Navigate to={principal?.role === 'admin' ? '/admin' : '/tenant'} replace />
}

function ForbiddenPage() {
  return <main style={{ padding: 'var(--space-6)' }}><h1>Access denied</h1></main>
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
          <Route path="/account" element={<AccountPage />} />
          
          {/* Tenant-only routes */}
          <Route element={<ProtectedRoute allowedRoles={['tenant_user']} />}>
            <Route path="/tenant" element={<TenantDashboard />} />
            <Route path="/services" element={<ServicesPage />} />
            <Route path="/services/:id" element={<ServiceDetailPage />} />
            <Route path="/allocations" element={<AllocationsPage />} />
            <Route path="/ddos" element={<DdosCoveragePage />} />
          </Route>

          {/* Admin-only routes */}
          <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/admin/services" element={<AdminServicesPage />} />
            <Route path="/admin/tenants" element={<TenantsPage />} />
            <Route path="/admin/users" element={<UsersPage />} />
            <Route path="/admin/allocations" element={<AllocationsPage />} />
            <Route path="/admin/feeds" element={<FeedsPage />} />
            <Route path="/admin/global-blacklist" element={<GlobalBlacklistPage />} />
            <Route path="/admin/ddos" element={<DdosProtectionPage />} />
            <Route path="/admin/alerting" element={<AlertingPage />} />
            <Route path="/admin/node" element={<NodeControlPage />} />
            <Route path="/admin/jobs" element={<JobBacklogPage />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

