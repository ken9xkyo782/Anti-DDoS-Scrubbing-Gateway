import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { JobBacklogPage } from './JobBacklogPage'
import { useJobs } from '../../../hooks/resources/useJobs'
import type { JobView } from '../../../api/types'

vi.mock('../../../hooks/resources/useJobs', () => ({
  useJobs: vi.fn(),
}))

vi.mock('../../../ui', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../ui')>()
  return {
    ...original,
    Select: ({
      options,
      value,
      onValueChange,
      'aria-label': ariaLabel,
      id,
      disabled,
    }: {
      options: { value: string; label: string }[]
      value?: string
      onValueChange?: (value: string) => void
      'aria-label'?: string
      id?: string
      disabled?: boolean
    }) => (
      <select
        id={id}
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onValueChange?.(e.target.value)}
        disabled={disabled}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    ),
  }
})

global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))

describe('JobBacklogPage', () => {
  const mockJobs: JobView[] = [
    {
      id: 'job-1',
      target_type: 'service',
      target_id: 'srv-123',
      version: 3,
      job_type: 'SERVICE_UPDATE',
      trigger: 'service',
      status: 'succeeded',
      error: null,
      attempts: 1,
      dispatched_at: '2026-07-15T08:00:00Z',
      created_at: '2026-07-15T07:59:50Z',
      started_at: '2026-07-15T07:59:55Z',
      finished_at: '2026-07-15T08:00:00Z',
    },
    {
      id: 'job-2',
      target_type: 'feed',
      target_id: 'feed-abc',
      version: 1,
      job_type: 'FEED_SYNC',
      trigger: 'feed_manual',
      status: 'failed',
      error: 'HTTP 502 connection refused by server',
      attempts: 3,
      dispatched_at: '2026-07-15T08:01:00Z',
      created_at: '2026-07-15T08:00:30Z',
      started_at: '2026-07-15T08:00:40Z',
      finished_at: '2026-07-15T08:01:00Z',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useJobs).mockReturnValue({
      data: mockJobs,
      isLoading: false,
      error: null,
    } as never)
  })

  afterEach(() => {
    cleanup()
  })

  it('renders page elements, job list, and error messages', () => {
    render(<JobBacklogPage />)

    expect(screen.getByText('Job Backlog')).toBeDefined()
    expect(screen.getByText(/Monitor status, execution attempts, and history/)).toBeDefined()
    expect(screen.getByLabelText('Filter by Status')).toBeDefined()

    // Check table content
    expect(screen.getByText('SERVICE UPDATE')).toBeDefined()
    expect(screen.getByText('service: srv-123')).toBeDefined()
    expect(screen.getByText('Version: 3')).toBeDefined()
    expect(screen.getAllByText('Succeeded').length).toBe(2)

    expect(screen.getByText('FEED SYNC')).toBeDefined()
    expect(screen.getByText('feed: feed-abc')).toBeDefined()
    expect(screen.getByText('Version: 1')).toBeDefined()
    expect(screen.getAllByText('Failed').length).toBe(2)
    expect(screen.getByText('HTTP 502 connection refused by server')).toBeDefined()
  })

  it('triggers a filter query on status change', () => {
    render(<JobBacklogPage />)

    const select = screen.getByLabelText('Filter by Status')
    fireEvent.change(select, { target: { value: 'failed' } })

    expect(useJobs).toHaveBeenLastCalledWith('failed')
  })

  it('renders loading state correctly', () => {
    vi.mocked(useJobs).mockReturnValue({
      data: [],
      isLoading: true,
      error: null,
    } as never)

    const { container } = render(<JobBacklogPage />)
    expect(container.querySelector('[class*="skeleton"]')).toBeInTheDocument()
  })

  it('renders error state correctly', () => {
    vi.mocked(useJobs).mockReturnValue({
      data: [],
      isLoading: false,
      error: { message: 'Database failure' },
    } as never)

    render(<JobBacklogPage />)
    expect(screen.getByText('Database failure')).toBeDefined()
  })
})
