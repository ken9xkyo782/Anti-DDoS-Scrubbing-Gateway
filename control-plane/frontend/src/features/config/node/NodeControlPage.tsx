import { useState } from 'react'
import {
  PageHeader,
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  Switch,
  ConfirmDialog,
  Dialog,
  Field,
  Input,
  Button,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import { useNodeControl } from '../../../hooks/resources/useNodeControl'

export function NodeControlPage() {
  const { healthQuery, bypassMutation, maintenanceMutation } = useNodeControl()
  const { data: health, isLoading, error } = healthQuery

  const [isBypassEnableOpen, setIsBypassEnableOpen] = useState(false)
  const [bypassReason, setBypassReason] = useState('')
  const [isBypassDisableOpen, setIsBypassDisableOpen] = useState(false)

  const [isMaintEnableOpen, setIsMaintEnableOpen] = useState(false)
  const [isMaintDisableOpen, setIsMaintDisableOpen] = useState(false)

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', padding: 'var(--space-12)' }}>
        Loading node health status...
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: 'var(--space-6)' }}>
        <div style={{ color: 'var(--color-danger, #b42318)', padding: 'var(--space-4)', border: '1px solid', borderRadius: 'var(--radius-md)' }}>
          Failed to load node status: {error.message}
        </div>
      </div>
    )
  }

  const bypass = health?.bypass || { desired: false, effective: false, activated_at: null, active_seconds: 0 }
  const maintenance = health?.maintenance || { desired: false, effective: false, activated_at: null, active_seconds: 0 }

  const handleBypassToggle = () => {
    if (bypass.desired) {
      setIsBypassDisableOpen(true)
    } else {
      setBypassReason('')
      setIsBypassEnableOpen(true)
    }
  }

  const handleMaintToggle = () => {
    if (maintenance.desired) {
      setIsMaintDisableOpen(true)
    } else {
      setIsMaintEnableOpen(true)
    }
  }

  const confirmBypassEnable = async () => {
    try {
      await bypassMutation.mutateAsync({ enabled: true, reason: bypassReason.trim() || undefined })
      toast({ title: 'Global scrubbing bypass enabled', variant: 'success' })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to enable bypass', description: msg, variant: 'error' })
    } finally {
      setIsBypassEnableOpen(false)
    }
  }

  const confirmBypassDisable = async () => {
    try {
      await bypassMutation.mutateAsync({ enabled: false })
      toast({ title: 'Global scrubbing bypass disabled', variant: 'success' })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to disable bypass', description: msg, variant: 'error' })
    } finally {
      setIsBypassDisableOpen(false)
    }
  }

  const confirmMaintEnable = async () => {
    try {
      await maintenanceMutation.mutateAsync({ enabled: true })
      toast({ title: 'Node maintenance mode enabled', variant: 'success' })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to enable maintenance mode', description: msg, variant: 'error' })
    } finally {
      setIsMaintEnableOpen(false)
    }
  }

  const confirmMaintDisable = async () => {
    try {
      await maintenanceMutation.mutateAsync({ enabled: false })
      toast({ title: 'Node maintenance mode disabled', variant: 'success' })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to disable maintenance mode', description: msg, variant: 'error' })
    } finally {
      setIsMaintDisableOpen(false)
    }
  }

  const formatDuration = (seconds: number) => {
    if (!seconds) return '0s'
    const hrs = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    const secs = seconds % 60
    const parts = []
    if (hrs > 0) parts.push(`${hrs}h`)
    if (mins > 0) parts.push(`${mins}m`)
    if (secs > 0 || parts.length === 0) parts.push(`${secs}s`)
    return parts.join(' ')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Node Control"
        description="Operational administration interface for the gateway node. Control emergency bypass and maintenance status."
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(350px, 1fr))', gap: 'var(--space-6)' }}>
        {/* Bypass Card */}
        <Card style={{ borderLeft: bypass.desired ? '4px solid var(--color-danger, #b42318)' : undefined }}>
          <CardHeader>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <CardTitle>Global Scrubbing Bypass</CardTitle>
              <Switch
                id="bypass-toggle"
                aria-label="Toggle Scrubbing Bypass"
                checked={bypass.desired}
                onCheckedChange={handleBypassToggle}
                disabled={bypassMutation.isPending}
              />
            </div>
          </CardHeader>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', lineHeight: '1.5' }}>
              Route all incoming traffic directly around the DDoS scrubbing core. <strong>WARNING:</strong> This disables scrubbing and exposes downstream systems directly to raw traffic. Use only in emergencies or when troubleshooting.
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)', marginTop: 'var(--space-2)' }}>
              <div>
                <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>Desired State</span>
                <Badge variant={bypass.desired ? 'danger' : 'default'}>
                  {bypass.desired ? 'Bypass Active' : 'Inactive'}
                </Badge>
              </div>
              <div>
                <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>Effective State</span>
                <Badge variant={bypass.effective ? 'danger' : 'default'}>
                  {bypass.effective ? 'Bypass Active' : 'Inactive'}
                </Badge>
              </div>
            </div>

            {bypass.effective && (
              <div style={{ backgroundColor: 'var(--bg-muted, #f9fafb)', padding: 'var(--space-3)', borderRadius: 'var(--radius-md)', fontSize: '0.875rem', display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                {bypass.activated_at && (
                  <div>
                    <span style={{ color: 'var(--text-muted)' }}>Activated: </span>
                    <span style={{ fontWeight: 500 }}>{new Date(bypass.activated_at).toLocaleString()}</span>
                  </div>
                )}
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Active Duration: </span>
                  <span style={{ fontWeight: 500 }}>{formatDuration(bypass.active_seconds)}</span>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Maintenance Card */}
        <Card style={{ borderLeft: maintenance.desired ? '4px solid var(--color-warning, #f59e0b)' : undefined }}>
          <CardHeader>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <CardTitle>Maintenance Mode</CardTitle>
              <Switch
                id="maintenance-toggle"
                aria-label="Toggle Maintenance Mode"
                checked={maintenance.desired}
                onCheckedChange={handleMaintToggle}
                disabled={maintenanceMutation.isPending}
              />
            </div>
          </CardHeader>
          <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', lineHeight: '1.5' }}>
              Puts the scrubbing node into maintenance mode. Any configuration updates received while in maintenance mode will be queued and applied automatically when the node exits maintenance mode.
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)', marginTop: 'var(--space-2)' }}>
              <div>
                <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>Desired State</span>
                <Badge variant={maintenance.desired ? 'warning' : 'default'}>
                  {maintenance.desired ? 'Maintenance' : 'Inactive'}
                </Badge>
              </div>
              <div>
                <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>Effective State</span>
                <Badge variant={maintenance.effective ? 'warning' : 'default'}>
                  {maintenance.effective ? 'Maintenance' : 'Inactive'}
                </Badge>
              </div>
            </div>

            {maintenance.effective && (
              <div style={{ backgroundColor: 'var(--bg-muted, #f9fafb)', padding: 'var(--space-3)', borderRadius: 'var(--radius-md)', fontSize: '0.875rem', display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                {maintenance.activated_at && (
                  <div>
                    <span style={{ color: 'var(--text-muted)' }}>Activated: </span>
                    <span style={{ fontWeight: 500 }}>{new Date(maintenance.activated_at).toLocaleString()}</span>
                  </div>
                )}
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Active Duration: </span>
                  <span style={{ fontWeight: 500 }}>{formatDuration(maintenance.active_seconds)}</span>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Bypass Enable Dialog (Custom for Reason) */}
      <Dialog
        open={isBypassEnableOpen}
        onOpenChange={setIsBypassEnableOpen}
        title="Enable Global Scrubbing Bypass?"
        description="WARNING: Enabling bypass disables scrubbing and routes all traffic directly around the scrubbing core. Downstream systems will be exposed to direct DDoS traffic."
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Field label="Reason for Bypass (Optional)">
            <Input
              value={bypassReason}
              onChange={(e) => setBypassReason(e.target.value)}
              placeholder="Provide a reason for the bypass (max 512 chars)..."
              maxLength={512}
              aria-label="Reason for bypass"
            />
          </Field>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-3)', marginTop: 'var(--space-2)' }}>
            <Button variant="secondary" onClick={() => setIsBypassEnableOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmBypassEnable} loading={bypassMutation.isPending}>
              Confirm Bypass
            </Button>
          </div>
        </div>
      </Dialog>

      {/* Bypass Disable Dialog */}
      <ConfirmDialog
        open={isBypassDisableOpen}
        onOpenChange={setIsBypassDisableOpen}
        title="Disable Node Bypass?"
        description="This will re-enable traffic scrubbing. Incoming traffic will be filtered by the active rules and lists."
        confirmLabel="Confirm"
        onConfirm={confirmBypassDisable}
        tone="info"
      />

      {/* Maintenance Enable Dialog */}
      <ConfirmDialog
        open={isMaintEnableOpen}
        onOpenChange={setIsMaintEnableOpen}
        title="Enable Maintenance Mode?"
        description="While maintenance mode is active, any new configuration changes (e.g. services, rules, whitelists) will be queued and will NOT take effect until maintenance mode is disabled (queue-and-apply-on-exit)."
        confirmLabel="Confirm"
        onConfirm={confirmMaintEnable}
        tone="info"
      />

      {/* Maintenance Disable Dialog */}
      <ConfirmDialog
        open={isMaintDisableOpen}
        onOpenChange={setIsMaintDisableOpen}
        title="Exit Maintenance Mode?"
        description="Exiting maintenance mode will apply all queued configuration updates immediately to the scrubbing core."
        confirmLabel="Confirm"
        onConfirm={confirmMaintDisable}
        tone="info"
      />
    </div>
  )
}
