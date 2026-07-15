import React from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import styles from './Sidebar.module.css'

export const Sidebar: React.FC = () => {
  const { principal } = useAuth()
  const role = principal?.role

  const getActiveClassName = ({ isActive }: { isActive: boolean }) =>
    `${styles.navLink} ${isActive ? styles.activeLink : ''}`

  return (
    <aside className={styles.sidebar} aria-label="Sidebar navigation">
      <div className={styles.brand}>
        <div className={styles.brandIcon} aria-hidden="true">Ω</div>
        <span className={styles.brandText}>Scrubbing Gateway</span>
      </div>

      <nav className={styles.navGroups}>
        {/* Overview Group */}
        <div className={styles.group}>
          <div className={styles.groupTitle}>Overview</div>
          <NavLink
            to={role === 'admin' ? '/admin' : '/tenant'}
            className={getActiveClassName}
            end
          >
            <span className={styles.navIcon}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
            </span>
            <span className={styles.navText}>Dashboard</span>
          </NavLink>
        </div>

        {/* Manage Group (Role-aware) */}
        <div className={styles.group}>
          <div className={styles.groupTitle}>Manage</div>
          {role === 'tenant_user' && (
            <>
              <NavLink to="/services" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>
                </span>
                <span className={styles.navText}>My Services</span>
              </NavLink>
              <NavLink to="/allocations" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><line x1="9" y1="9" x2="15" y2="9"/><line x1="9" y1="15" x2="15" y2="15"/><line x1="9" y1="12" x2="15" y2="12"/></svg>
                </span>
                <span className={styles.navText}>Allocations</span>
              </NavLink>
            </>
          )}

          {role === 'admin' && (
            <>
              <NavLink to="/admin/services" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>
                </span>
                <span className={styles.navText}>Services</span>
              </NavLink>
              <NavLink to="/admin/tenants" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                </span>
                <span className={styles.navText}>Tenants</span>
              </NavLink>
              <NavLink to="/admin/users" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                </span>
                <span className={styles.navText}>Users</span>
              </NavLink>
              <NavLink to="/admin/allocations" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><line x1="9" y1="9" x2="15" y2="9"/><line x1="9" y1="15" x2="15" y2="15"/><line x1="9" y1="12" x2="15" y2="12"/></svg>
                </span>
                <span className={styles.navText}>Allocations</span>
              </NavLink>
              <NavLink to="/admin/feeds" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg>
                </span>
                <span className={styles.navText}>Threat Feeds</span>
              </NavLink>
              <NavLink to="/admin/global-blacklist" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
                </span>
                <span className={styles.navText}>Global Blacklist</span>
              </NavLink>
              <NavLink to="/admin/alerting" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
                </span>
                <span className={styles.navText}>Alerting</span>
              </NavLink>
              <NavLink to="/admin/node" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="15" x2="23" y2="15"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="15" x2="4" y2="15"/></svg>
                </span>
                <span className={styles.navText}>Node Control</span>
              </NavLink>
              <NavLink to="/admin/jobs" className={getActiveClassName}>
                <span className={styles.navIcon}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                </span>
                <span className={styles.navText}>Job Backlog</span>
              </NavLink>
            </>
          )}
        </div>

        {/* Observe Group */}
        <div className={styles.group}>
          <div className={styles.groupTitle}>Observe</div>
          <NavLink
            to={role === 'admin' ? '/admin' : '/tenant'}
            className={getActiveClassName}
            end
          >
            <span className={styles.navIcon}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
            </span>
            <span className={styles.navText}>Telemetry</span>
          </NavLink>
          <NavLink to="/billing" className={getActiveClassName}>
            <span className={styles.navIcon}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
            </span>
            <span className={styles.navText}>Billing</span>
          </NavLink>
          <NavLink to="/alerts" className={getActiveClassName}>
            <span className={styles.navIcon}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            </span>
            <span className={styles.navText}>Alerts</span>
          </NavLink>
        </div>
      </nav>
    </aside>
  )
}
