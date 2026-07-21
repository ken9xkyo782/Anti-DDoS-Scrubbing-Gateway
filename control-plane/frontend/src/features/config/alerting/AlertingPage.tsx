import React, { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  EmptyState,
  ConfirmDialog,
  Badge,
  Tabs,
  Field,
  Input,
  Select,
  Switch,
  NumberInput,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import { ApiError, fieldErrorsFrom422 } from '../../../api/client'
import { useAlertRules, useUpdateAlertRule } from '../../../hooks/resources/useAlertRules'
import {
  useNotificationChannels,
  useCreateNotificationChannel,
  useUpdateNotificationChannel,
  useDeleteNotificationChannel,
  useTestNotificationChannel,
} from '../../../hooks/resources/useNotificationChannels'
import type {
  AlertRuleResponse,
  NotificationChannelResponse,
  NotificationChannelRequest,
  AlertChannelTestResponse,
  WebhookChannelConfig,
  EmailChannelConfig,
  ChannelKind,
  AlertSeverity,
} from '../../../api/types'

export function AlertingPage() {
  const { data: rules = [], isLoading: isLoadingRules, error: rulesError } = useAlertRules()
  const { data: channels = [], isLoading: isLoadingChannels, error: channelsError } = useNotificationChannels()

  const [activeTab, setActiveTab] = useState('rules')
  const [editingRule, setEditingRule] = useState<AlertRuleResponse | null>(null)
  const [editingChannel, setEditingChannel] = useState<NotificationChannelResponse | null>(null)
  const [deletingChannel, setDeletingChannel] = useState<NotificationChannelResponse | null>(null)
  const [isCreateChannelOpen, setIsCreateChannelOpen] = useState(false)

  // Testing status
  const [testingStatus, setTestingStatus] = useState<{
    channelId: string
    state: 'loading' | 'success' | 'error'
    errorMsg?: string | null
  } | null>(null)

  const createChannelMutation = useCreateNotificationChannel()
  const updateChannelMutation = useUpdateNotificationChannel(editingChannel?.id ?? '')
  const deleteChannelMutation = useDeleteNotificationChannel(deletingChannel?.id ?? '')

  const handleCreateChannelSubmit = async (payload: NotificationChannelRequest) => {
    await createChannelMutation.mutateAsync(payload)
    toast({ title: 'Notification channel created successfully', variant: 'success' })
    setIsCreateChannelOpen(false)
  }

  const handleDeleteChannel = async () => {
    if (!deletingChannel) return
    try {
      await deleteChannelMutation.mutateAsync()
      toast({ title: 'Notification channel deleted successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to delete channel', description: message, variant: 'error' })
    } finally {
      setDeletingChannel(null)
    }
  }

  const getSeverityBadge = (severity: AlertSeverity) => {
    switch (severity) {
      case 'critical':
        return <Badge variant="danger">Critical</Badge>
      case 'warning':
        return <Badge variant="warning">Warning</Badge>
      case 'info':
      default:
        return <Badge variant="info">Info</Badge>
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Alerting Configuration"
        description="Configure global alert rules thresholds, severity, maintenance silences, and notification channels."
      />

      <Tabs.Root value={activeTab} onValueChange={setActiveTab}>
        <Tabs.List>
          <Tabs.Trigger value="rules">Alert Rules</Tabs.Trigger>
          <Tabs.Trigger value="channels">Notification Channels</Tabs.Trigger>
        </Tabs.List>

        <div style={{ marginTop: 'var(--space-4)' }}>
          <Tabs.Content value="rules">
            <DataTable<AlertRuleResponse>
              columns={[
                {
                  key: 'key',
                  header: 'Rule Key',
                  render: (rule) => <span style={{ fontWeight: 600 }}>{rule.key}</span>,
                },
                {
                  key: 'enabled',
                  header: 'Status',
                  render: (rule) => (
                    <Badge variant={rule.enabled ? 'success' : 'default'}>
                      {rule.enabled ? 'Enabled' : 'Disabled'}
                    </Badge>
                  ),
                },
                {
                  key: 'severity',
                  header: 'Severity',
                  render: (rule) => getSeverityBadge(rule.severity),
                },
                {
                  key: 'fire_threshold',
                  header: 'Fire Threshold',
                  render: (rule) => <span>{rule.fire_threshold}</span>,
                },
                {
                  key: 'clear_threshold',
                  header: 'Clear Threshold',
                  render: (rule) => <span>{rule.clear_threshold}</span>,
                },
                {
                  key: 'silence_in_maintenance',
                  header: 'Silence in Maintenance',
                  render: (rule) => (
                    <span>{rule.silence_in_maintenance ? 'Yes' : 'No'}</span>
                  ),
                },
              ]}
              data={rules}
              isLoading={isLoadingRules}
              error={rulesError?.message}
              rowActions={(rule) => (
                <Button variant="secondary" size="sm" onClick={() => setEditingRule(rule)}>
                  Edit
                </Button>
              )}
            />
          </Tabs.Content>

          <Tabs.Content value="channels">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Button variant="primary" onClick={() => setIsCreateChannelOpen(true)}>
                  Add Channel
                </Button>
              </div>

              {testingStatus && (
                <div
                  style={{
                    padding: 'var(--space-3)',
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid',
                    backgroundColor:
                      testingStatus.state === 'loading'
                        ? 'var(--color-bg-info, #e0f2fe)'
                        : testingStatus.state === 'success'
                        ? 'var(--color-bg-success, #d1fae5)'
                        : 'var(--color-bg-danger, #fee2e2)',
                    color:
                      testingStatus.state === 'loading'
                        ? 'var(--color-text-info, #0369a1)'
                        : testingStatus.state === 'success'
                        ? 'var(--color-text-success, #047857)'
                        : 'var(--color-text-danger, #b91c1c)',
                    borderColor:
                      testingStatus.state === 'loading'
                        ? 'var(--color-border-info, #7dd3fc)'
                        : testingStatus.state === 'success'
                        ? 'var(--color-border-success, #6ee7b7)'
                        : 'var(--color-border-danger, #fca5a5)',
                    fontSize: '14px',
                  }}
                  role="status"
                >
                  {testingStatus.state === 'loading' && 'Sending test notification...'}
                  {testingStatus.state === 'success' && 'Test notification sent successfully!'}
                  {testingStatus.state === 'error' &&
                    `Failed to send test notification: ${testingStatus.errorMsg}`}
                </div>
              )}

              <DataTable<NotificationChannelResponse>
                columns={[
                  {
                    key: 'name',
                    header: 'Channel Name',
                    render: (channel) => <span style={{ fontWeight: 600 }}>{channel.name}</span>,
                  },
                  {
                    key: 'kind',
                    header: 'Kind',
                    render: (channel) => (
                      <Badge variant="info">{channel.kind === 'email' ? 'Email (SMTP)' : 'Webhook'}</Badge>
                    ),
                  },
                  {
                    key: 'enabled',
                    header: 'Status',
                    render: (channel) => (
                      <Badge variant={channel.enabled ? 'success' : 'default'}>
                        {channel.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                    ),
                  },
                  {
                    key: 'min_severity',
                    header: 'Min Severity',
                    render: (channel) => getSeverityBadge(channel.min_severity),
                  },
                  {
                    key: 'config',
                    header: 'Configuration',
                    render: (channel) => {
                      if (channel.kind === 'webhook') {
                        const cfg = channel.config as WebhookChannelConfig
                        return (
                          <span style={{ fontSize: '12px', color: 'var(--text-muted)', wordBreak: 'break-all' }}>
                            URL: {cfg.url}
                          </span>
                        )
                      } else {
                        const cfg = channel.config as EmailChannelConfig
                        return (
                          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                            <div>Host: {cfg.smtp_host}</div>
                            <div>From: {cfg.from}</div>
                            <div style={{ wordBreak: 'break-all' }}>
                              To: {Array.isArray(cfg.to) ? cfg.to.join(', ') : ''}
                            </div>
                          </div>
                        )
                      }
                    },
                  },
                ]}
                data={channels}
                isLoading={isLoadingChannels}
                error={channelsError?.message}
                emptyState={
                  <EmptyState
                    title="No notification channels configured"
                    description="Configure Webhook or Email SMTP destinations to receive real-time alerting notifications."
                    action={
                      <Button variant="primary" onClick={() => setIsCreateChannelOpen(true)}>
                        Add Channel
                      </Button>
                    }
                  />
                }
                rowActions={(channel) => (
                  <ChannelRowActions
                    channel={channel}
                    onEdit={setEditingChannel}
                    onDelete={setDeletingChannel}
                    onTestStart={(id) => setTestingStatus({ channelId: id, state: 'loading' })}
                    onTestResult={(id, result, error) => {
                      if (error) {
                        const msg = error instanceof Error ? error.message : 'Unknown network error'
                        setTestingStatus({ channelId: id, state: 'error', errorMsg: msg })
                        toast({ title: 'Failed to send test notification', description: msg, variant: 'error' })
                      } else if (result && result.state === 'success') {
                        setTestingStatus({ channelId: id, state: 'success' })
                        toast({ title: 'Test notification sent successfully!', variant: 'success' })
                      } else {
                        const errorMsg = result?.error || 'Unknown dispatch error'
                        setTestingStatus({ channelId: id, state: 'error', errorMsg })
                        toast({
                          title: 'Failed to send test notification',
                          description: result?.error || undefined,
                          variant: 'error',
                        })
                      }
                    }}
                  />
                )}
              />
            </div>
          </Tabs.Content>
        </div>
      </Tabs.Root>

      {/* Edit Rule Dialog */}
      <Dialog
        open={editingRule !== null}
        onOpenChange={(open) => {
          if (!open) setEditingRule(null)
        }}
        title={editingRule ? `Configure Alert Rule: ${editingRule.key}` : 'Configure Alert Rule'}
      >
        {editingRule && (
          <RuleForm rule={editingRule} onCancel={() => setEditingRule(null)} />
        )}
      </Dialog>

      {/* Create Channel Dialog */}
      <Dialog
        open={isCreateChannelOpen}
        onOpenChange={setIsCreateChannelOpen}
        title="Add Notification Channel"
      >
        <ChannelForm
          onSubmit={handleCreateChannelSubmit}
          onCancel={() => setIsCreateChannelOpen(false)}
        />
      </Dialog>

      {/* Edit Channel Dialog */}
      <Dialog
        open={editingChannel !== null}
        onOpenChange={(open) => {
          if (!open) setEditingChannel(null)
        }}
        title="Edit Notification Channel"
      >
        {editingChannel && (
          <ChannelForm
            channel={editingChannel}
            onSubmit={async (payload) => {
              await updateChannelMutation.mutateAsync(payload)
              toast({ title: 'Notification channel updated successfully', variant: 'success' })
              setEditingChannel(null)
            }}
            onCancel={() => setEditingChannel(null)}
          />
        )}
      </Dialog>

      {/* Delete Channel Confirmation */}
      <ConfirmDialog
        open={deletingChannel !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingChannel(null)
        }}
        title="Delete Notification Channel"
        description={`Are you sure you want to delete notification channel "${deletingChannel?.name}"? Real-time alert dispatches to this channel will stop immediately. This action cannot be undone.`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDeleteChannel}
      />
    </div>
  )
}

interface ChannelRowActionsProps {
  channel: NotificationChannelResponse
  onEdit: (channel: NotificationChannelResponse) => void
  onDelete: (channel: NotificationChannelResponse) => void
  onTestStart: (id: string) => void
  onTestResult: (id: string, result: AlertChannelTestResponse | null, error: unknown) => void
}

function ChannelRowActions({ channel, onEdit, onDelete, onTestStart, onTestResult }: ChannelRowActionsProps) {
  const testMutation = useTestNotificationChannel(channel.id)
  const [isTesting, setIsTesting] = useState(false)

  const handleTest = async () => {
    setIsTesting(true)
    onTestStart(channel.id)
    try {
      const result = await testMutation.mutateAsync()
      onTestResult(channel.id, result, null)
    } catch (err) {
      onTestResult(channel.id, null, err)
    } finally {
      setIsTesting(false)
    }
  }

  return (
    <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
      <Button variant="secondary" size="sm" onClick={handleTest} loading={isTesting}>
        Test
      </Button>
      <Button variant="secondary" size="sm" onClick={() => onEdit(channel)}>
        Edit
      </Button>
      <Button variant="danger" size="sm" onClick={() => onDelete(channel)}>
        Delete
      </Button>
    </div>
  )
}

interface RuleFormProps {
  rule: AlertRuleResponse
  onCancel: () => void
}

function RuleForm({ rule, onCancel }: RuleFormProps) {
  const [enabled, setEnabled] = useState(rule.enabled)
  const [severity, setSeverity] = useState<AlertSeverity>(rule.severity)
  const [fireThreshold, setFireThreshold] = useState(rule.fire_threshold.toString())
  const [clearThreshold, setClearThreshold] = useState(rule.clear_threshold.toString())
  const [silenceInMaintenance, setSilenceInMaintenance] = useState(rule.silence_in_maintenance)

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const updateMutation = useUpdateAlertRule(rule.key)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    const parsedFire = parseFloat(fireThreshold)
    if (fireThreshold.trim() === '' || isNaN(parsedFire)) {
      nextErrors.fire_threshold = 'Fire threshold must be a valid number'
    }
    const parsedClear = parseFloat(clearThreshold)
    if (clearThreshold.trim() === '' || isNaN(parsedClear)) {
      nextErrors.clear_threshold = 'Clear threshold must be a valid number'
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    setIsSubmitting(true)
    try {
      await updateMutation.mutateAsync({
        enabled,
        severity,
        fire_threshold: parsedFire,
        clear_threshold: parsedClear,
        silence_in_maintenance: silenceInMaintenance,
      })
      toast({ title: 'Alert rule updated successfully', variant: 'success' })
      onCancel()
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422 && err.detail) {
          setErrors(fieldErrorsFrom422(err.detail))
        } else {
          setSubmitError(err.message)
        }
      } else if (err instanceof Error) {
        setSubmitError(err.message)
      } else {
        setSubmitError('An unexpected error occurred')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const severityOptions = [
    { value: 'info', label: 'Info' },
    { value: 'warning', label: 'Warning' },
    { value: 'critical', label: 'Critical' },
  ]

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div
          style={{
            color: 'var(--color-danger, #b42318)',
            padding: 'var(--space-2)',
            border: '1px solid',
            borderRadius: 'var(--radius-md)',
          }}
          role="alert"
        >
          {submitError}
        </div>
      )}

      <Field label="Rule Severity">
        <Select
          options={severityOptions}
          value={severity}
          onValueChange={(val) => setSeverity(val as AlertSeverity)}
          disabled={isSubmitting}
        />
      </Field>

      <Field label="Fire Threshold" error={errors.fire_threshold} required>
        <NumberInput
          value={fireThreshold}
          onChange={(e) => setFireThreshold(e.target.value)}
          disabled={isSubmitting}
          aria-label="Fire Threshold"
        />
      </Field>

      <Field label="Clear Threshold" error={errors.clear_threshold} required>
        <NumberInput
          value={clearThreshold}
          onChange={(e) => setClearThreshold(e.target.value)}
          disabled={isSubmitting}
          aria-label="Clear Threshold"
        />
      </Field>

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', margin: 'var(--space-2) 0' }}>
        <Switch
          id="rule-enabled"
          checked={enabled}
          onCheckedChange={setEnabled}
          disabled={isSubmitting}
        />
        <label htmlFor="rule-enabled" style={{ fontWeight: 500, cursor: 'pointer' }}>
          Rule Enabled
        </label>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', margin: 'var(--space-2) 0' }}>
        <Switch
          id="rule-silence"
          checked={silenceInMaintenance}
          onCheckedChange={setSilenceInMaintenance}
          disabled={isSubmitting}
        />
        <label htmlFor="rule-silence" style={{ fontWeight: 500, cursor: 'pointer' }}>
          Silence in Maintenance
        </label>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          Save
        </Button>
      </div>
    </form>
  )
}

interface ChannelFormProps {
  channel?: NotificationChannelResponse
  onSubmit: (payload: NotificationChannelRequest) => Promise<void>
  onCancel: () => void
}

function ChannelForm({ channel, onSubmit, onCancel }: ChannelFormProps) {
  const [name, setName] = useState(channel?.name ?? '')
  const [kind, setKind] = useState<ChannelKind>(channel?.kind ?? 'webhook')
  const [enabled, setEnabled] = useState(channel?.enabled ?? true)
  const [minSeverity, setMinSeverity] = useState<AlertSeverity>(channel?.min_severity ?? 'info')

  // Config values (config is Record<string, unknown>; narrow by channel kind).
  const webhookCfg = (channel?.config ?? {}) as WebhookChannelConfig
  const emailCfg = (channel?.config ?? {}) as EmailChannelConfig
  const [webhookUrl, setWebhookUrl] = useState(channel?.kind === 'webhook' ? webhookCfg.url ?? '' : '')
  const [smtpHost, setSmtpHost] = useState(channel?.kind === 'email' ? emailCfg.smtp_host ?? '' : '')
  const [smtpFrom, setSmtpFrom] = useState(channel?.kind === 'email' ? emailCfg.from ?? '' : '')
  const [smtpTo, setSmtpTo] = useState(
    channel?.kind === 'email' && Array.isArray(emailCfg.to)
      ? emailCfg.to.join(', ')
      : ''
  )

  // Write-only Secret field
  const [secret, setSecret] = useState('')

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const isEdit = !!channel

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrors({})
    setSubmitError(null)

    const nextErrors: Record<string, string> = {}
    if (!name.trim()) {
      nextErrors.name = 'Channel name is required'
    }

    const config: Record<string, unknown> = {}

    if (kind === 'webhook') {
      if (!webhookUrl.trim()) {
        nextErrors.webhook_url = 'Webhook URL is required'
      } else {
        config.url = webhookUrl.trim()
      }
    } else {
      if (!smtpHost.trim()) {
        nextErrors.smtp_host = 'SMTP Host is required'
      }
      if (!smtpFrom.trim()) {
        nextErrors.smtp_from = 'Sender address is required'
      }
      if (!smtpTo.trim()) {
        nextErrors.smtp_to = 'At least one recipient is required'
      }

      if (smtpHost.trim() && smtpFrom.trim() && smtpTo.trim()) {
        config.smtp_host = smtpHost.trim()
        config.from = smtpFrom.trim()
        config.to = smtpTo
          .split(',')
          .map((email) => email.trim())
          .filter((email) => email.length > 0)
      }
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    setIsSubmitting(true)
    try {
      const payload: NotificationChannelRequest = {
        name: name.trim(),
        kind,
        enabled,
        min_severity: minSeverity,
        config,
      }

      // Write-only logic: only send secret if user entered a value
      if (secret.trim()) {
        payload.secret = secret.trim()
      }

      await onSubmit(payload)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422 && err.detail) {
          setErrors(fieldErrorsFrom422(err.detail))
        } else {
          setSubmitError(err.message)
        }
      } else if (err instanceof Error) {
        setSubmitError(err.message)
      } else {
        setSubmitError('An unexpected error occurred')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const kindOptions = [
    { value: 'webhook', label: 'Webhook' },
    { value: 'email', label: 'Email (SMTP)' },
  ]

  const severityOptions = [
    { value: 'info', label: 'Info' },
    { value: 'warning', label: 'Warning' },
    { value: 'critical', label: 'Critical' },
  ]

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {submitError && (
        <div
          style={{
            color: 'var(--color-danger, #b42318)',
            padding: 'var(--space-2)',
            border: '1px solid',
            borderRadius: 'var(--radius-md)',
          }}
          role="alert"
        >
          {submitError}
        </div>
      )}

      <Field label="Channel Name" error={errors.name} required>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Slack Alerts"
          disabled={isSubmitting}
          aria-label="Channel Name"
        />
      </Field>

      <Field label="Channel Kind">
        <Select
          options={kindOptions}
          value={kind}
          onValueChange={(val) => setKind(val as ChannelKind)}
          disabled={isSubmitting || isEdit}
          aria-label="Channel Kind"
        />
      </Field>

      <Field label="Min Severity">
        <Select
          options={severityOptions}
          value={minSeverity}
          onValueChange={(val) => setMinSeverity(val as AlertSeverity)}
          disabled={isSubmitting}
        />
      </Field>

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', margin: 'var(--space-2) 0' }}>
        <Switch
          id="channel-enabled"
          checked={enabled}
          onCheckedChange={setEnabled}
          disabled={isSubmitting}
        />
        <label htmlFor="channel-enabled" style={{ fontWeight: 500, cursor: 'pointer' }}>
          Channel Enabled
        </label>
      </div>

      {kind === 'webhook' && (
        <Field label="Webhook URL" error={errors.webhook_url} required>
          <Input
            value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)}
            placeholder="e.g. https://hooks.slack.com/services/..."
            disabled={isSubmitting}
          />
        </Field>
      )}

      {kind === 'email' && (
        <>
          <Field label="SMTP Host" error={errors.smtp_host} required>
            <Input
              value={smtpHost}
              onChange={(e) => setSmtpHost(e.target.value)}
              placeholder="e.g. smtp.mailtrap.io"
              disabled={isSubmitting}
              aria-label="SMTP Host"
            />
          </Field>

          <Field label="Sender" error={errors.smtp_from} required>
            <Input
              value={smtpFrom}
              onChange={(e) => setSmtpFrom(e.target.value)}
              placeholder="e.g. alerts@company.com"
              disabled={isSubmitting}
              aria-label="Sender"
            />
          </Field>

          <Field label="Recipients (comma separated)" error={errors.smtp_to} required>
            <Input
              value={smtpTo}
              onChange={(e) => setSmtpTo(e.target.value)}
              placeholder="e.g. oncall@company.com, pager@company.com"
              disabled={isSubmitting}
              aria-label="Recipients"
            />
          </Field>
        </>
      )}

      <Field label="Secret / Token (Write-only)">
        <Input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder={isEdit ? 'Leave blank to keep existing secret' : 'Optional secret / token key'}
          disabled={isSubmitting}
          aria-label="Secret"
        />
      </Field>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-4)' }}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={isSubmitting}>
          Save
        </Button>
      </div>
    </form>
  )
}
