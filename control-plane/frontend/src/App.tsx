import { Navigate, Route, Routes } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import { AppLayout } from './layout/AppLayout'
import { LoginPage } from './pages/LoginPage'
import { TenantDashboard } from './pages/TenantDashboard'
import { ProtectedRoute } from './routes/ProtectedRoute'

function DashboardLanding() {
  const { principal } = useAuth()

  return <Navigate to={principal?.role === 'admin' ? '/admin' : '/tenant'} replace />
}

function AdminDashboardPlaceholder() {
  return <h1>Admin dashboard</h1>
}

function ForbiddenPage() {
  return <main><h1>Access denied</h1></main>
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forbidden" element={<ForbiddenPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route index element={<DashboardLanding />} />
          <Route element={<ProtectedRoute allowedRoles={['tenant_user']} />}>
            <Route path="/tenant" element={<TenantDashboard />} />
          </Route>
          <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
            <Route path="/admin" element={<AdminDashboardPlaceholder />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
