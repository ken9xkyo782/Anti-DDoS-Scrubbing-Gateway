import React from 'react'
import styles from './Spinner.module.css'

export interface SpinnerProps extends React.HTMLAttributes<HTMLDivElement> {
  size?: 'sm' | 'md' | 'lg'
}

export const Spinner: React.FC<SpinnerProps> = ({ className = '', size = 'md', ...props }) => {
  return (
    <div className={`${styles.spinnerContainer} ${className}`} {...props}>
      <div className={`${styles.spinner} ${styles[size]}`} role="status" aria-label="Loading..." />
    </div>
  )
}
