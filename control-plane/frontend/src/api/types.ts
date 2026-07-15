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

export type BlacklistScope = 'service' | 'global'

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
  service_id: string | null
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

