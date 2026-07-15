import { Link, useParams } from 'react-router-dom'
import {
  PageHeader,
  Tabs,
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Spinner,
  StatusBadge,
} from '../../../ui'
import { useService } from '../../../hooks/resources/useServices'
import { useApplyStatus } from '../../../hooks/useApplyStatus'
import { RulesTab } from './RulesTab'
import { WhitelistTab } from './WhitelistTab'
import { BlacklistTab } from './BlacklistTab'

export function ServiceDetailPage() {
  const { id } = useParams<{ id: string }>()
  const serviceId = id ?? null

  const { data: service, isLoading, error } = useService(serviceId)
  const { data: applyStatusView } = useApplyStatus(serviceId)

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '200px' }}>
        <Spinner size="lg" />
      </div>
    )
  }

  if (error || !service) {
    return (
      <div style={{ padding: 'var(--space-6)' }}>
        <PageHeader
          title="Service Not Found"
          breadcrumb={
            <Link to="/services" style={{ textDecoration: 'none', color: 'var(--text-muted)' }}>
              &larr; Back to Services
            </Link>
          }
        />
        <div style={{ color: 'var(--color-danger, #b42318)', padding: 'var(--space-4)', border: '1px solid', borderRadius: 'var(--radius-md)', marginTop: 'var(--space-4)' }}>
          {error?.message || 'The requested service could not be loaded.'}
        </div>
      </div>
    )
  }

  const currentStatus = applyStatusView?.apply_status ?? service.apply_status
  const isUpdating = currentStatus === 'pending' || currentStatus === 'queued' || currentStatus === 'applying'

  const breadcrumb = (
    <Link to="/services" style={{ textDecoration: 'none', color: 'var(--text-muted)', fontSize: 'var(--font-size-sm)' }}>
      &larr; Back to Services
    </Link>
  )

  const headerActions = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
      <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)' }}>Status:</span>
      <StatusBadge status={currentStatus} />
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title={service.name}
        description={`Manage policy rules, whitelists, and blacklists for CIDR block ${service.cidr_or_ip}`}
        breadcrumb={breadcrumb}
        actions={headerActions}
      />

      {isUpdating && (
        <div style={{
          backgroundColor: 'var(--bg-elevated)',
          borderLeft: '4px solid var(--color-info, #0969da)',
          padding: 'var(--space-4)',
          borderRadius: 'var(--radius-sm)',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)'
        }}>
          <Spinner size="sm" />
          <span style={{ fontSize: 'var(--font-size-sm)', fontWeight: 500 }}>
            Applying configuration changes to scrubbing gateway nodes...
          </span>
        </div>
      )}

      {applyStatusView?.apply_status === 'failed' && applyStatusView.last_error && (
        <div style={{
          backgroundColor: 'rgba(180, 35, 24, 0.1)',
          borderLeft: '4px solid var(--color-danger, #b42318)',
          padding: 'var(--space-4)',
          borderRadius: 'var(--radius-sm)',
          fontSize: 'var(--font-size-sm)',
          color: 'var(--color-danger, #b42318)'
        }}>
          <strong>Gateway deployment failed:</strong> {applyStatusView.last_error}. Active configuration has been rolled back.
        </div>
      )}

      <Tabs.Root defaultValue="overview">
        <Tabs.List>
          <Tabs.Trigger value="overview">Overview</Tabs.Trigger>
          <Tabs.Trigger value="rules">Rules</Tabs.Trigger>
          <Tabs.Trigger value="whitelist">Whitelist</Tabs.Trigger>
          <Tabs.Trigger value="blacklist">Blacklist</Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="overview">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 'var(--space-4)' }}>
            <Card>
              <CardHeader>
                <CardTitle>Configuration Details</CardTitle>
              </CardHeader>
              <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: 'var(--space-2)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Service Name:</span>
                  <span style={{ fontWeight: 600 }}>{service.name}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: 'var(--space-2)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>CIDR/IP Address:</span>
                  <span style={{ fontFamily: 'monospace' }}>{service.cidr_or_ip}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: 'var(--space-2)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Service Mode:</span>
                  <span style={{ textTransform: 'capitalize' }}>{service.mode.replace(/-/g, ' ')}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Service Status:</span>
                  <span style={{ fontWeight: 500, color: service.enabled ? 'var(--color-success, #1a7f37)' : 'var(--text-muted)' }}>
                    {service.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Bandwidth & VIP Limits</CardTitle>
              </CardHeader>
              <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: 'var(--space-2)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>VIP PPS Limit:</span>
                  <span>{service.vip_pps != null ? service.vip_pps.toLocaleString() : 'Unlimited'}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: 'var(--space-2)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>VIP BPS Limit:</span>
                  <span>{service.vip_bps != null ? `${(service.vip_bps / 1_000_000).toFixed(1)} Mbps` : 'Unlimited'}</span>
                </div>
                {service.plan && (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: 'var(--space-2)' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Committed Bandwidth:</span>
                      <span>{service.plan.committed_clean_gbps} Gbps</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Ceiling Bandwidth:</span>
                      <span>{service.plan.ceiling_clean_gbps} Gbps</span>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        </Tabs.Content>

        <Tabs.Content value="rules">
          <RulesTab serviceId={service.id} />
        </Tabs.Content>

        <Tabs.Content value="whitelist">
          <WhitelistTab serviceId={service.id} service={service} />
        </Tabs.Content>

        <Tabs.Content value="blacklist">
          <BlacklistTab serviceId={service.id} />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  )
}
