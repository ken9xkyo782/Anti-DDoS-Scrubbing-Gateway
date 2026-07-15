import React from 'react'
import { Button } from '../Button/Button'
import styles from './Pagination.module.css'

export interface PaginationProps {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
  disabled?: boolean
}

export const Pagination: React.FC<PaginationProps> = ({ currentPage, totalPages, onPageChange, disabled = false }) => {
  return (
    <div className={styles.pagination}>
      <Button
        variant="secondary"
        size="sm"
        disabled={currentPage <= 1 || disabled}
        onClick={() => onPageChange(currentPage - 1)}
      >
        Previous
      </Button>
      <span className={styles.info}>
        Page {currentPage} of {totalPages || 1}
      </span>
      <Button
        variant="secondary"
        size="sm"
        disabled={currentPage >= totalPages || disabled}
        onClick={() => onPageChange(currentPage + 1)}
      >
        Next
      </Button>
    </div>
  )
}
