import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DataTable, StatusBadge, Pagination } from './index'

afterEach(cleanup)

describe('Data Display Primitives', () => {
  describe('DataTable', () => {
    interface TestData {
      id: string
      name: string
      value: number
    }

    const columns = [
      { key: 'name', header: 'Name', sortable: true },
      { key: 'value', header: 'Value', render: (row: TestData) => `Count: ${row.value}` },
    ]

    const data: TestData[] = [
      { id: '1', name: 'Alpha', value: 10 },
      { id: '2', name: 'Beta', value: 20 },
    ]

    it('renders header names and row content', () => {
      render(<DataTable columns={columns} data={data} />)

      expect(screen.getByText('Name')).toBeInTheDocument()
      expect(screen.getByText('Value')).toBeInTheDocument()
      expect(screen.getByText('Alpha')).toBeInTheDocument()
      expect(screen.getByText('Count: 20')).toBeInTheDocument()
    })

    it('triggers onSort when sorting is clicked', () => {
      const handleSort = vi.fn()
      render(
        <DataTable
          columns={columns}
          data={data}
          sortColumn="name"
          sortDirection="asc"
          onSort={handleSort}
        />
      )

      const header = screen.getByText('Name')
      fireEvent.click(header)
      expect(handleSort).toHaveBeenCalledWith('name', 'desc')
    })

    it('displays skeleton loaders when loading', () => {
      const { container } = render(<DataTable columns={columns} data={data} isLoading />)
      const skeletons = container.querySelectorAll('[class*="skeleton"]')
      expect(skeletons.length).toBeGreaterThan(0)
      expect(screen.queryByText('Alpha')).not.toBeInTheDocument()
    })

    it('renders emptyState when empty', () => {
      render(
        <DataTable
          columns={columns}
          data={[]}
          emptyState={<div>Custom Empty State</div>}
        />
      )
      expect(screen.getByText('Custom Empty State')).toBeInTheDocument()
    })
  })

  describe('StatusBadge', () => {
    const statuses = [
      { status: 'active', expectedClass: 'success' },
      { status: 'failed', expectedClass: 'danger' },
      { status: 'applying', expectedClass: 'info' },
      { status: 'pending', expectedClass: 'warning' },
      { status: 'queued', expectedClass: 'warning' },
      { status: 'unknown', expectedClass: 'default' },
    ]

    statuses.forEach(({ status, expectedClass }) => {
      it(`renders status "${status}" with variant style "${expectedClass}"`, () => {
        render(<StatusBadge status={status} />)
        const element = screen.getByText(status.charAt(0).toUpperCase() + status.slice(1))
        expect(element).toBeInTheDocument()
        expect(element.className).toContain(expectedClass)
      })
    })
  })

  describe('Pagination', () => {
    it('disables previous button on first page and triggers change on next', () => {
      const handleChange = vi.fn()
      render(
        <Pagination
          currentPage={1}
          totalPages={3}
          onPageChange={handleChange}
        />
      )

      expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next' })).not.toBeDisabled()

      fireEvent.click(screen.getByRole('button', { name: 'Next' }))
      expect(handleChange).toHaveBeenCalledWith(2)
    })

    it('disables next button on last page and triggers change on prev', () => {
      const handleChange = vi.fn()
      render(
        <Pagination
          currentPage={3}
          totalPages={3}
          onPageChange={handleChange}
        />
      )

      expect(screen.getByRole('button', { name: 'Previous' })).not.toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()

      fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
      expect(handleChange).toHaveBeenCalledWith(2)
    })
  })
})
