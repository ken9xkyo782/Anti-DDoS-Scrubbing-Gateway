import { Navigate, Outlet } from 'react-router-dom'

import { type Role, useAuth } from '../auth/AuthContext'

interface ProtectedRouteProps {
  allowedRoles?: readonly Role[]
}

export function ProtectedRoute({ allowedRoles }: ProtectedRouteProps) {
  const { isLoading, principal } = useAuth()

  if (isLoading) {
    return <p>Loading session…</p>
  }

  if (principal === null) {
    return <Navigate to="/login" replace />
  }

  if (allowedRoles !== undefined && !allowedRoles.includes(principal.role)) {
    return <Navigate to="/forbidden" replace />
  }

  return <Outlet />
}
