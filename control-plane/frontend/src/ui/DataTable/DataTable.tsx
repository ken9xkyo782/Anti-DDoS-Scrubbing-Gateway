import React from 'react'
import { Skeleton } from '../Skeleton/Skeleton'
import styles from './DataTable.module.css'

export interface Column<T> {
  key: string
  header: React.ReactNode
  render?: (row: T) => React.ReactNode
  sortable?: boolean
}

export interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[]
  isLoading?: boolean
  error?: string
  emptyState?: React.ReactNode
  rowActions?: (row: T) => React.ReactNode
  sortColumn?: string
  sortDirection?: 'asc' | 'desc'
  onSort?: (columnKey: string, direction: 'asc' | 'desc') => void
}

export function DataTable<T>({
  columns,
  data,
  isLoading = false,
  error,
  emptyState,
  rowActions,
  sortColumn,
  sortDirection,
  onSort,
}: DataTableProps<T>) {
  const handleSortClick = (columnKey: string, sortable?: boolean) => {
    if (!sortable || !onSort) return
    const direction = sortColumn === columnKey && sortDirection === 'asc' ? 'desc' : 'asc'
    onSort(columnKey, direction)
  }

  if (error) {
    return (
      <div className={styles.errorContainer} role="alert">
        <p className={styles.errorText}>{error}</p>
      </div>
    )
  }

  return (
    <div className={styles.tableWrapper}>
      <table className={styles.table}>
        <thead className={styles.thead}>
          <tr className={styles.headerRow}>
            {columns.map((col) => (
              <th
                key={col.key}
                className={`${styles.th} ${col.sortable ? styles.sortableTh : ''}`}
                onClick={() => handleSortClick(col.key, col.sortable)}
                style={{ cursor: col.sortable ? 'pointer' : 'default' }}
              >
                <div className={styles.thContent}>
                  {col.header}
                  {col.sortable && sortColumn === col.key && (
                    <span className={styles.sortIndicator} aria-hidden="true">
                      {sortDirection === 'asc' ? ' ▲' : ' ▼'}
                    </span>
                  )}
                </div>
              </th>
            ))}
            {rowActions && <th className={styles.th} aria-label="Actions" />}
          </tr>
        </thead>
        <tbody className={styles.tbody}>
          {isLoading ? (
            Array.from({ length: 5 }).map((_, rowIndex) => (
              <tr key={rowIndex} className={styles.tr}>
                {columns.map((col) => (
                  <td key={col.key} className={styles.td}>
                    <Skeleton className={styles.skeletonCell} />
                  </td>
                ))}
                {rowActions && (
                  <td className={styles.td}>
                    <Skeleton className={styles.skeletonCellActions} />
                  </td>
                )}
              </tr>
            ))
          ) : data.length === 0 ? (
            <tr className={styles.emptyRow}>
              <td colSpan={columns.length + (rowActions ? 1 : 0)} className={styles.tdEmpty}>
                {emptyState || (
                  <div className={styles.defaultEmpty}>
                    No data available
                  </div>
                )}
              </td>
            </tr>
          ) : (
            data.map((row, rowIndex) => (
              <tr key={rowIndex} className={styles.tr}>
                {columns.map((col) => (
                  <td key={col.key} className={styles.td}>
                    {col.render ? col.render(row) : (row[col.key as keyof T] as unknown as React.ReactNode)}
                  </td>
                ))}
                {rowActions && (
                  <td className={styles.tdActions}>
                    {rowActions(row)}
                  </td>
                )}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
