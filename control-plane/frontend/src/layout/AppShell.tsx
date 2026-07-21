import React from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { NodeControlBanner } from '../components/NodeControlBanner'
import styles from './AppShell.module.css'

export const AppShell: React.FC = () => {
  return (
    <div className={styles.layoutContainer}>
      <Sidebar />
      <div className={styles.contentArea}>
        <NodeControlBanner />
        <Topbar />
        <main className={styles.mainContent}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
