import React from 'react'
import styles from './Skeleton.module.css'

export type SkeletonProps = React.HTMLAttributes<HTMLDivElement>

export const Skeleton: React.FC<SkeletonProps> = ({ className = '', ...props }) => {
  return <div className={`${styles.skeleton} ${className}`} {...props} />
}
