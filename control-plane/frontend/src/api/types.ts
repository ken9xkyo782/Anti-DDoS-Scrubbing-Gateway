export type Role = 'admin' | 'tenant_user'

export type UserStatus = 'active' | 'disabled'

export type TenantStatus = 'active' | 'suspended'

export type CIDRStatus = 'active' | 'revoked'

export type ApplyStatus = 'pending' | 'queued' | 'applying' | 'active' | 'failed'

export type JobStatus = 'queued' | 'applying' | 'succeeded' | 'failed' | 'superseded'

export type JobType = 'SERVICE_UPDATE' | 'FEED_SYNC' | 'GLOBAL_DENY_APPLY'

export type ChangeTrigger =
  | 'service'
  | 'plan'
  | 'rule'
  | 'whitelist'
  | 'blacklist'
  | 'enable'
  | 'disable'
  | 'feed_manual'
  | 'feed_schedule'
  | 'feed_delete'
  | 'feed_dry_run'
  | 'global_deny_retry'

export type ServiceMode = 'allow-rule-only'

export type Protocol = 'tcp' | 'udp' | 'icmp' | 'any'

export type BlacklistScope = 'global'

export type BlacklistSource = 'manual' | 'feed'

// DTO Response Interfaces

export interface JobView {
  id: string
  target_type: string
  target_id: string
  version: number
  job_type: JobType
  trigger: ChangeTrigger
  status: JobStatus
  error: string | null
  attempts: number
  dispatched_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ApplyStatusView {
  service_id: string
  tenant_id: string
  tenant_name: string | null
  apply_status: ApplyStatus
  version: number
  active_version: number | null
  last_error: string | null
  last_applied_at: string | null
  latest_job: JobView | null
}

export interface ApplyMutationResponse {
  apply_status: ApplyStatus
  version: number
  active_version: number | null
}

export interface RuleResponse {
  id: string
  service_id: string
  priority: number
  protocol: Protocol
  src_port_lo: number | null
  src_port_hi: number | null
  dst_port_lo: number | null
  dst_port_hi: number | null
  pps: number | null
  bps: number | null
  enabled: boolean
  warnings: string[]
  created_at: string
  updated_at: string
}

export interface RuleOverlapCheckResponse {
  warnings: string[]
}

export interface ServicePlanResponse {
  committed_clean_gbps: number
  ceiling_clean_gbps: number
  billing_metric: string
  overage_policy: string
}

export interface ServiceResponse {
  id: string
  tenant_id: string
  tenant_name: string | null
  created_by: string | null
  creator_username: string | null
  name: string
  cidr_or_ip: string
  mode: ServiceMode
  enabled: boolean
  vip_pps: number | null
  vip_bps: number | null
  apply_status: ApplyStatus
  version: number
  active_version: number | null
  plan: ServicePlanResponse
  warnings: string[]
  created_at: string
  updated_at: string
}

export interface WhitelistEntryResponse {
  id: string
  service_id: string
  source_cidr: string
  created_by: string | null
  created_at: string
}

export interface BlacklistEntryResponse {
  id: string
  scope: BlacklistScope
  source: BlacklistSource
  source_cidr: string
  created_by: string | null
  created_at: string
}

export interface TenantResponse {
  id: string
  name: string
  status: TenantStatus
  created_at: string
  updated_at: string
  active_allocation_count: number
  user_count: number
}

export interface UserResponse {
  id: string
  username: string
  role: Role
  tenant_id: string | null
  tenant_name: string | null
  status: UserStatus
  last_login_at: string | null
}

export interface AllocationResponse {
  id: string
  tenant_id: string
  cidr: string
  status: CIDRStatus
  allocated_by: string | null
  created_at: string
  updated_at: string
}

export interface AllocationUsageResponse {
  allocation: AllocationResponse
  dependent_count: number
}

export interface OverlapCheckResponse {
  overlaps: boolean
  conflicts: AllocationResponse[]
}

export type FeedFormat = 'line_list'

export type FeedSyncStatus = 'queued' | 'running' | 'success' | 'partial' | 'failed'

export interface FeedSourceResponse {
  id: string
  name: string
  url: string
  format: FeedFormat
  enabled: boolean
  sync_interval_seconds: number
  has_credential: boolean
  last_status: FeedSyncStatus | null
  last_error: string | null
  last_sync_at: string | null
  next_sync_at: string | null
  created_at: string
  updated_at: string
}

export interface FeedSyncRunResponse {
  id: string
  feed_source_id: string
  source_name: string
  sequence: number
  trigger: ChangeTrigger
  dry_run: boolean
  status: FeedSyncStatus
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  error: string | null
  fetched_lines: number
  valid: number
  duplicates: number
  added: number
  removed: number
  skipped_invalid: number
  overlap_count: number
  global_changed: boolean
  desired_revision: number | null
  node_map_version: number | null
}

export interface FeedSyncJobResponse {
  id: string
  feed_sync_run_id: string
  status: JobStatus
  attempts: number
  dispatched_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface FeedSyncAccepted {
  run: FeedSyncRunResponse
  job: FeedSyncJobResponse
}

export type AlertSeverity = 'info' | 'warning' | 'critical'
export type ChannelKind = 'email' | 'webhook'

export interface AlertRuleResponse {
  key: string
  enabled: boolean
  severity: AlertSeverity
  fire_threshold: number
  clear_threshold: number
  silence_in_maintenance: boolean
}

export interface AlertRulePatchRequest {
  enabled?: boolean | null
  severity?: AlertSeverity | null
  fire_threshold?: number | null
  clear_threshold?: number | null
  silence_in_maintenance?: boolean | null
}

export interface WebhookChannelConfig {
  url?: string
}

export interface EmailChannelConfig {
  smtp_host?: string
  from?: string
  to?: string[]
}

export interface NotificationChannelResponse {
  id: string
  name: string
  kind: ChannelKind
  tenant_id: string | null
  enabled: boolean
  min_severity: AlertSeverity
  config: Record<string, unknown>
}

export interface NotificationChannelRequest {
  name?: string
  kind?: ChannelKind
  tenant_id?: string | null
  enabled?: boolean
  min_severity?: AlertSeverity
  config?: Record<string, unknown>
  secret?: string | null
}

export interface AlertChannelTestResponse {
  state: string
  attempts: number
  error: string | null
}

export interface BlockedPortResponse {
  port: number
  note: string | null
  created_by: string | null
  created_at: string
}

export interface AmplificationConfigResponse {
  hardcoded_ports: number[]
  dynamic_ports: BlockedPortResponse[]
}



