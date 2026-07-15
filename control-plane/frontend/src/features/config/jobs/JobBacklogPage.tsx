import { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Badge,
  Select,
  Field,
  EmptyState,
} from '../../../ui'
import { useJobs } from '../../../hooks/resources/useJobs'
import type { JobView, JobStatus } from '../../../api/types'

export function JobBacklogPage() {
  const [statusFilter, setStatusFilter] = useState<JobStatus | ''>('')
  const { data: jobs = [], isLoading, error } = useJobs(statusFilter || null)

  const getJobStatusVariant = (status: string) => {
    switch (status) {
      case 'succeeded':
        return 'success'
      case 'failed':
        return 'danger'
      case 'applying':
        return 'info'
      case 'queued':
        return 'warning'
      default:
        return 'default'
    }
  }

  const statusOptions = [
    { value: '', label: 'All Statuses' },
    { value: 'queued', label: 'Queued' },
    { value: 'applying', label: 'Applying' },
    { value: 'succeeded', label: 'Succeeded' },
    { value: 'failed', label: 'Failed' },
    { value: 'superseded', label: 'Superseded' },
  ]

  const columns = [
    {
      key: 'job_type',
      header: 'Job Type',
      render: (job: JobView) => (
        <span style={{ fontWeight: 600, fontSize: 'var(--font-size-sm)' }}>
          {job.job_type.replace(/_/g, ' ')}
        </span>
      ),
    },
    {
      key: 'target',
      header: 'Target',
      render: (job: JobView) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          <span style={{ fontSize: 'var(--font-size-sm)', fontFamily: 'monospace' }}>
            {job.target_type}: {job.target_id}
          </span>
          <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>
            Version: {job.version}
          </span>
        </div>
      ),
    },
    {
      key: 'trigger',
      header: 'Trigger',
      render: (job: JobView) => (
        <span style={{ fontSize: 'var(--font-size-sm)' }}>
          {job.trigger.replace(/_/g, ' ')}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (job: JobView) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
          <Badge variant={getJobStatusVariant(job.status)}>
            {job.status.charAt(0).toUpperCase() + job.status.slice(1)}
          </Badge>
          {job.error && (
            <span
              style={{
                fontSize: 'var(--font-size-xs)',
                color: 'var(--color-danger, #b42318)',
                maxWidth: '250px',
                wordBreak: 'break-all',
              }}
            >
              {job.error}
            </span>
          )}
        </div>
      ),
    },
    {
      key: 'attempts',
      header: 'Attempts',
      render: (job: JobView) => <span>{job.attempts}</span>,
    },
    {
      key: 'created_at',
      header: 'Created At',
      render: (job: JobView) => (
        <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-muted)' }}>
          {new Date(job.created_at).toLocaleString()}
        </span>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Job Backlog"
        description="Monitor status, execution attempts, and history of system configuration application jobs."
      />

      <div style={{ maxWidth: '300px', alignSelf: 'flex-start' }}>
        <Field label="Filter by Status">
          <Select
            options={statusOptions}
            value={statusFilter}
            onValueChange={(val) => setStatusFilter(val as JobStatus | '')}
            aria-label="Filter by Status"
          />
        </Field>
      </div>

      <DataTable<JobView>
        columns={columns}
        data={jobs}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No jobs found"
            description={
              statusFilter
                ? `No jobs match the status "${statusFilter}".`
                : 'There are currently no background jobs in the log.'
            }
          />
        }
      />
    </div>
  )
}
