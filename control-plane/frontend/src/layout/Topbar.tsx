import React from 'react'
import { useLocation, useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ThemeToggle } from './ThemeToggle'
import { ApplyStatusIndicator } from './ApplyStatusIndicator'
import { DropdownMenu } from '../ui'
import styles from './Topbar.module.css'

export const Topbar: React.FC = () => {
  const { principal, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  // Breadcrumbs generator
  const getBreadcrumbs = () => {
    const path = location.pathname
    if (path === '/admin' || path === '/tenant') {
      return ['Overview', 'Dashboard']
    }
    if (path === '/billing') {
      return ['Observe', 'Billing']
    }
    if (path === '/alerts') {
      return ['Observe', 'Alerts']
    }
    if (path === '/account') {
      return ['Settings', 'Account']
    }
    if (path === '/services') {
      return ['Manage', 'My Services']
    }
    if (path === '/allocations') {
      return ['Manage', 'Allocations']
    }
    if (path === '/admin/services') {
      return ['Manage', 'Services Oversight']
    }
    if (path === '/admin/tenants') {
      return ['Manage', 'Tenants']
    }
    if (path === '/admin/users') {
      return ['Manage', 'Users']
    }
    if (path === '/admin/allocations') {
      return ['Manage', 'Allocations']
    }
    if (path === '/admin/feeds') {
      return ['Manage', 'Threat Feeds']
    }
    if (path === '/admin/global-blacklist') {
      return ['Manage', 'Global Blacklist']
    }
    if (path === '/admin/alerting') {
      return ['Manage', 'Alerting Config']
    }
    if (path === '/admin/node') {
      return ['Manage', 'Node Control']
    }
    if (path === '/admin/jobs') {
      return ['Manage', 'Job Backlog']
    }
    // Fallback split path
    const parts = path.split('/').filter(Boolean)
    return parts.map(p => p.charAt(0).toUpperCase() + p.slice(1))
  };

  const breadcrumbs = getBreadcrumbs()

  return (
    <header className={styles.topbar}>
      <div className={styles.left}>
        <nav aria-label="Breadcrumb" className={styles.breadcrumbs}>
          {breadcrumbs.map((crumb, idx) => (
            <React.Fragment key={crumb}>
              {idx > 0 && <span className={styles.breadcrumbSeparator} aria-hidden="true">/</span>}
              <span className={idx === breadcrumbs.length - 1 ? styles.breadcrumbActive : ''}>
                {crumb}
              </span>
            </React.Fragment>
          ))}
        </nav>
      </div>

      <div className={styles.right}>
        <div className={styles.actions}>
          <ApplyStatusIndicator />
          <ThemeToggle />
        </div>

        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className={styles.profileMenu} aria-label="User menu">
              <span className={styles.username}>{principal?.username}</span>
              <span
                className={`${styles.badge} ${
                  principal?.role === 'admin' ? styles.adminBadge : styles.tenantBadge
                }`}
              >
                {principal?.role === 'admin' ? 'Admin' : 'Tenant'}
              </span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content align="end">
              <DropdownMenu.Item asChild>
                <Link to="/account" style={{ textDecoration: 'none', color: 'inherit' }}>
                  Account Settings
                </Link>
              </DropdownMenu.Item>
              <DropdownMenu.Separator />
              <DropdownMenu.Item onClick={handleLogout} className="text-danger">
                Sign out
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
    </header>
  )
}
