import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { NodeControlBanner } from '../components/NodeControlBanner'

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
      <NodeControlBanner />
      <header>
        <nav aria-label="Primary navigation">
          <NavLink to={dashboardPath}>Dashboard</NavLink>
          <NavLink to="/billing">Billing</NavLink>
          <NavLink to="/alerts">Alerts</NavLink>
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
