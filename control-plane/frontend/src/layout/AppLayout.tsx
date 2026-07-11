import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

export function AppLayout() {
  const { logout, principal } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  const dashboardPath = principal?.role === 'admin' ? '/admin' : '/tenant'

  return (
    <div>
      <header>
        <nav aria-label="Primary navigation">
          <NavLink to={dashboardPath}>Dashboard</NavLink>
          <span>{principal?.username}</span>
          <button type="button" onClick={() => void handleLogout()}>
            Sign out
          </button>
        </nav>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  )
}
