import React from 'react'
import styles from './Badge.module.css'

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'success' | 'warning' | 'danger' | 'info'
}

export const Badge: React.FC<BadgeProps> = ({ className = '', variant = 'default', ...props }) => {
  return <span className={`${styles.badge} ${styles[variant]} ${className}`} {...props} />
}
