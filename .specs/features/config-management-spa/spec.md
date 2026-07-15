# Configuration Management SPA (Admin & Tenant) Specification

**Feature ID prefix:** `CFG`
**Milestone:** Cross-cutting frontend — completes the deferred *"Config CRUD screens in the SPA"* effort (telemetry-dashboards `D-TEL-2` / out-of-scope row). Extends the M5 SPA shell; recommend placement as a frontend feature after M6 (see *Roadmap note*).
**Status:** Spec drafted (2026-07-15, awaiting approval → Design)

## Problem Statement

Every configuration surface of the gateway — services, allow-rules, whitelist/VIP, blacklists, tenants, CIDR allocations, threat feeds, alert rules/channels, and node bypass/maintenance — is fully implemented and tested at the **API** layer (M1, M4, M6), but the SPA that shipped with M5 is **read-only observability** (telemetry, health, billing, alert *history*). There is **no way to change configuration from the browser**: a tenant cannot create a service or edit an allow-rule, and an admin cannot allocate a CIDR, add a threat feed, or configure an alert channel without hitting the raw API. M5 explicitly deferred this as a *"separate later frontend effort"* ([telemetry-dashboards/spec.md:28](../telemetry-dashboards/spec.md)). This feature closes that gap by adding role-aware **management screens** on top of the existing, tested APIs — making the Pilot operable end-to-end from the dashboard for both the paying tenant (self-service) and the system admin (provisioning + operations).

## Goals

- [ ] A **tenant_user** can self-manage their own services, allow-rules, whitelist/VIP, and service blacklist entirely from the SPA — no API client required.
- [ ] An **admin** can manage tenants, users, CIDR allocations, threat feeds, the global blacklist, alert rules/channels, and node bypass/maintenance from the SPA.
- [ ] Every mutating action surfaces the **async apply lifecycle** (`pending → queued → applying → active | failed`) and the `version` / `active_version` contract — the UI never implies a config change is instantly live; a `failed` apply is visible and the last-good version is shown as still active.
- [ ] **Zero new backend endpoints** — the feature is pure frontend over the already-shipped, tested control-plane API. Any screen that would require a missing endpoint is flagged, not built here.
- [ ] **Strict tenant isolation** preserved in the UI (5.2): a tenant never sees or edits another tenant's configuration; admin-only screens are unreachable for tenants.
- [ ] Reuses the existing SPA foundation (Vite + React + TS, TanStack Query, `apiClient`, `AuthContext`, `ProtectedRoute`, `AppLayout`, role-aware routing) — extends the shell, does not fork it.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Any new/changed **backend** endpoint, model, or migration | Pure frontend feature; all CRUD APIs already exist and are tested (M1/M4/M6). A missing endpoint is a flag for a separate backend task, not built here. |
| Telemetry / node-health / trend / top-talker **views** | Already shipped (M5 *Telemetry & dashboards*, VERIFIED). This feature adds config screens beside them, not new observability. |
| Billing / chargeback **views** | Already shipped (`BillingPanel`, M5 *Chargeback metering*). |
| Alert **history** view | Already shipped (`AlertsPanel`). This feature adds the alert **config** side (rules thresholds + channels + test-send). |
| Audit-log viewer / query / export | Owned by M6 *SLA/OLA reporting & audit* (`/audit` router not yet built). Add there, not here. |
| Server-push transport (SSE/WebSocket) for apply-status | REST polling is the project standard (`D-TEL-3`); apply-status is surfaced by polling `/services/{id}/apply-status`. |
| SSO / IdP / MFA, multi-admin separation-of-duties | GA track (CM-10 / OP-07). |
| Guided onboarding / config "learning mode" wizard | GA track (OP-08). |
| Native mobile app | Web SPA only; responsive layout is sufficient. |

---

## User Stories

### P1: Config SPA shell & role-aware navigation ⭐ MVP

**User Story**: As a logged-in user, I want a management area added to the dashboard with navigation that matches my role, so that I can reach the config screens I'm allowed to use.

**Why P1**: Foundational. Every management screen hangs off this shell, and the reusable async-apply feedback + form primitives defined here are consumed by all other stories.

**Acceptance Criteria**:

1. WHEN a user is authenticated THEN the SPA SHALL render a management navigation section within the existing `AppLayout`, showing only the destinations permitted for the user's role (`tenant_user` → self-service config; `admin` → admin console), reusing `AuthContext`/`ProtectedRoute`.
2. WHEN a `tenant_user` attempts to reach an admin-only route (by URL) THEN the SPA SHALL block it via `ProtectedRoute allowedRoles={['admin']}` and redirect to `/forbidden` (existing pattern), never rendering admin data.
3. WHEN any management mutation returns `202 {apply_status, version, active_version}` THEN the SPA SHALL provide a reusable apply-status feedback primitive that displays the current lifecycle state and polls `/services/{id}/apply-status` until it reaches a terminal state (`active` or `failed`).
4. WHEN a management screen loads, is empty, or errors THEN it SHALL render consistent loading / empty / error states (reusing the conventions already used by the telemetry panels).
5. WHEN the session expires during a management action THEN the SPA SHALL redirect to login (existing `apiClient`/`ProtectedRoute` behavior), without losing the user's role-aware entry point on re-login.
6. WHEN a destructive action is invoked (delete/revoke/disable) THEN the UI SHALL require an explicit confirmation step before calling the API (mirrors the API's disable-first / confirm posture, `AD-002`).

**Independent Test**: Log in as a tenant → see only self-service nav, and a direct hit on an admin URL redirects to `/forbidden`; log in as admin → see the admin console nav. Trigger a mutation and observe the apply-status primitive transition to a terminal state.

---

### P1: Tenant service self-service ⭐ MVP

**User Story**: As a tenant_user, I want to create, edit, enable/disable, and delete my own protected services from the SPA, so that I can manage what the gateway protects without touching the API.

**Why P1**: The tenant is the paying user; service management is the core self-service need and the cleanest vertical slice (uniform ownership-guarded APIs).

**Acceptance Criteria**:

1. WHEN a tenant opens the services screen THEN it SHALL list their own services (`GET /services`) with name, `cidr_or_ip`, mode, enabled state, `apply_status`, `version`/`active_version`, and VIP ceiling (`vip_pps`/`vip_bps`).
2. WHEN a tenant creates a service THEN the SPA SHALL submit `POST /services` (name, `cidr_or_ip`, mode, optional VIP pps/bps) scoped to their own tenant (no `tenant_id` field for tenants), validate the CIDR/IP client-side, and surface the returned `202` apply-status.
3. WHEN a tenant edits a service THEN the SPA SHALL submit `PATCH /services/{id}` and surface the async apply-status.
4. WHEN a tenant enables or disables a service THEN the SPA SHALL call `POST /services/{id}/enable` / `POST /services/{id}/disable`, warn that disable = drop-all for that service, and require confirmation on disable.
5. WHEN a tenant deletes a service THEN the SPA SHALL require confirmation, call `DELETE /services/{id}`, and communicate the disable-first/cascade-children semantics.
6. WHEN the service form shows plan sizing (`committed_clean_gbps` / `ceiling_clean_gbps`) THEN it SHALL display it **read-only for tenants** (plan sizing is admin-governed via the admin-only `PATCH /services/{id}/plan`); the tenant form SHALL NOT offer editable committed/ceiling fields.
7. WHEN a service API call fails validation (e.g. CIDR ⊄ tenant allocation, overlap with another active service, reserved range) THEN the SPA SHALL surface the API's error message inline against the offending field rather than a generic failure.
8. WHEN a tenant has no services THEN the screen SHALL show an empty state with a create call-to-action.

**Independent Test**: As a tenant, create a service (watch apply-status progress), edit it, disable it (confirm the warning), then delete it; confirm committed/ceiling are read-only and a bad CIDR surfaces the API error inline.

---

### P1: Tenant allow-rule management ⭐ MVP

**User Story**: As a tenant_user, I want to manage a service's allow-rules from the SPA, so that I control which traffic is admitted.

**Why P1**: Allow-rules are the primary policy knob per service; without them a service is default-deny.

**Acceptance Criteria**:

1. WHEN a tenant opens a service's rules screen THEN it SHALL list allow-rules ordered by ascending `priority` (`GET /services/{id}/rules`), showing protocol, ports, priority, and action.
2. WHEN a tenant creates a rule THEN the SPA SHALL submit `POST /services/{id}/rules` (`202` async), enforcing client-side the ≤16-rules-per-service and unique-priority constraints before submit, and surfacing the API error if the server rejects.
3. WHEN a tenant edits or deletes a rule THEN the SPA SHALL call `PATCH` / `DELETE /services/{id}/rules/{rule_id}` and surface the async apply-status.
4. WHEN a tenant edits rule matching fields THEN the SPA SHALL offer the overlap-check preview (`POST /services/{id}/rules/overlap-check`) and display any first-match/shadowing warnings before the change is committed.
5. WHEN rules are displayed THEN the UI SHALL make first-match-by-priority semantics visible (order = evaluation order) so a tenant understands why a lower-priority rule may never match.

**Independent Test**: As a tenant, add three rules with distinct priorities, reorder/edit one and see the overlap-check warning, delete one, and confirm the list reflects ascending-priority evaluation order.

---

### P1: Tenant whitelist/VIP & service blacklist management ⭐ MVP

**User Story**: As a tenant_user, I want to manage my service's whitelist/VIP and blacklist entries from the SPA, so that I can bypass trusted sources and block bad ones.

**Why P1**: Completes the tenant self-service config triad (services + rules + lists) into a demoable slice.

**Acceptance Criteria**:

1. WHEN a tenant opens a service's whitelist screen THEN it SHALL list whitelist/VIP entries (`GET /services/{id}/whitelist`) with source CIDR and VIP ceiling context.
2. WHEN a tenant adds a whitelist/VIP entry THEN the SPA SHALL submit `POST` to the whitelist endpoint, validate the source CIDR client-side, and surface the async apply-status.
3. WHEN a tenant removes a whitelist entry THEN the SPA SHALL require confirmation and call the whitelist `DELETE` endpoint.
4. WHEN a tenant opens a service's blacklist screen THEN it SHALL list service-scoped blacklist entries (`GET /services/{id}/blacklist`) and support add (`POST`) / remove (`DELETE`) with the same validation + apply-status treatment.
5. WHEN a list entry is added that the API rejects (bad CIDR, duplicate) THEN the SPA SHALL surface the API error inline.

**Independent Test**: As a tenant, add and remove a whitelist entry and a blacklist entry for a service, confirming validation, confirmation-on-delete, and apply-status feedback.

---

### P2: Admin tenant & user management

**User Story**: As an admin, I want to manage tenants and users from the SPA, so that I can onboard/offboard organizations and their logins.

**Why P2**: Essential admin provisioning, but the tenant self-service slice (P1) proves the end-to-end pattern first.

**Acceptance Criteria**:

1. WHEN an admin opens the tenants screen THEN it SHALL list tenants (`GET /tenants`) with status, and support create (`POST /tenants`), edit (`PATCH /tenants/{id}`), and delete (`DELETE /tenants/{id}`, confirmation required — cascades per M1 rules).
2. WHEN an admin suspends or reactivates a tenant THEN the SPA SHALL call `POST /tenants/{id}/suspend` / `POST /tenants/{id}/reactivate` and reflect the new status.
3. WHEN an admin opens the users screen THEN it SHALL list users (`GET /users`) and support create (`POST /users`), edit (`PATCH /users/{id}`), delete (`DELETE /users/{id}`, confirmation), and reset-password (`POST /users/{id}/reset-password`).
4. WHEN an admin creates/edits a user THEN the SPA SHALL let the admin assign role and owning tenant, honoring the API's validation.
5. WHEN a delete/suspend affects a tenant with active services/allocations THEN the SPA SHALL surface the API's blocking error (e.g. revoke-in-use) rather than silently failing.

**Independent Test**: As admin, create a tenant, create a user in it, suspend then reactivate the tenant, reset the user's password, and delete a disposable tenant with confirmation.

---

### P2: Admin CIDR allocation management

**User Story**: As an admin, I want to allocate and revoke tenant CIDR ranges from the SPA, so that tenants have address space their services can protect.

**Why P2**: Prerequisite address space for tenant services; admin-owned.

**Acceptance Criteria**:

1. WHEN an admin opens the allocations screen THEN it SHALL list allocations with usage (`GET /allocations`) per tenant.
2. WHEN an admin allocates a CIDR THEN the SPA SHALL submit the allocation `POST`, run the overlap-check (`POST /allocations/overlap-check`) to warn on global overlap before commit, and surface validation errors.
3. WHEN an admin revokes an allocation THEN the SPA SHALL require confirmation, call `POST /allocations/{id}/revoke`, and surface the API's revoke-in-use block (`TCA-16`) if the range backs an active service.
4. WHEN a tenant views their own allocations (read-only) THEN the SPA MAY surface `GET /me/allocations` so a tenant sees the address space available to their services.

**Independent Test**: As admin, allocate a CIDR (see the overlap-check), then attempt to revoke one that backs an active service and confirm the block is surfaced.

---

### P2: Admin service oversight & plan sizing

**User Story**: As an admin, I want to view services across all tenants and set their committed/ceiling clean-Gbps plans, so that I govern the commercial commitments the tenant cannot self-set.

**Why P2**: The admin-only counterpart to tenant service self-service; `PATCH /services/{id}/plan` is admin-gated.

**Acceptance Criteria**:

1. WHEN an admin opens the services oversight screen THEN it SHALL list services across tenants (`GET /services` returns all for admin), filterable by tenant, with owning tenant, state, and current plan.
2. WHEN an admin sizes a plan THEN the SPA SHALL submit `PATCH /services/{id}/plan` (`committed_clean_gbps` / `ceiling_clean_gbps`) and surface the async apply-status.
3. WHEN an admin creates a service on behalf of a tenant THEN the service create form SHALL include the required `tenant_id` selector (admin path requires it) and MAY include plan sizing at create time.
4. WHEN an admin manages any service's lifecycle (enable/disable/delete) THEN the same ownership-agnostic endpoints apply (admin passes the ownership guard for any service).

**Independent Test**: As admin, list all services, filter by a tenant, set a committed/ceiling plan on one and watch apply-status, and create a service for a specific tenant.

---

### P2: Admin threat-feed & global blacklist management

**User Story**: As an admin, I want to manage threat-feed sources and the global blacklist from the SPA, so that I control node-wide deny intelligence.

**Why P2**: Operational deny-intel control; admin-owned, node-global.

**Acceptance Criteria**:

1. WHEN an admin opens the feeds screen THEN it SHALL list feed sources (`GET /feeds`) with enabled state, interval, and last-sync summary.
2. WHEN an admin creates/edits/deletes a feed source THEN the SPA SHALL call `POST /feeds` / `PUT /feeds/{id}` / `DELETE /feeds/{id}` (confirmation on delete) with client-side validation of URL and interval range.
3. WHEN an admin triggers a manual sync THEN the SPA SHALL call `POST /feeds/{id}/sync` and reflect the resulting run.
4. WHEN an admin opens a feed's sync history THEN it SHALL show `GET /feeds/{id}/syncs` (per-run stats, success/failure).
5. WHEN an admin manages the global blacklist THEN the SPA SHALL support add (`POST /blacklist`), list (`GET /blacklist`), and remove (`DELETE /blacklist`) with confirmation on remove.

**Independent Test**: As admin, add a feed source, trigger a manual sync, inspect its run history, then add and remove a global-blacklist entry.

---

### P2: Admin alerting configuration

**User Story**: As an admin, I want to tune alert-rule thresholds and manage notification channels from the SPA, so that alerting matches the node's operating envelope.

**Why P2**: Complements the existing read-only `AlertsPanel` (history) with the config side. Depends on M6 *Alerting* being executed.

**Acceptance Criteria**:

1. WHEN an admin opens the alert-rules screen THEN it SHALL list rules (`GET /alerts/rules`) with their §9.1-seeded thresholds and support per-rule threshold override (`PATCH /alerts/rules/{key}`).
2. WHEN an admin manages notification channels THEN the SPA SHALL support list (`GET /alerts/channels`), create (`POST /alerts/channels`), edit (`PATCH /alerts/channels/{id}`), and delete (`DELETE /alerts/channels/{id}`, confirmation).
3. WHEN an admin edits a channel secret (SMTP/webhook credential) THEN the field SHALL be write-only in the UI (never rendering the stored secret back), consistent with the API's write-only secret handling.
4. WHEN an admin tests a channel THEN the SPA SHALL call `POST /alerts/channels/{id}/test` and display the send result.

**Independent Test**: As admin, override an alert-rule threshold, create a webhook channel, send a test to it and see the result, then delete it.

---

### P2: Admin node bypass & maintenance controls

**User Story**: As an admin, I want to toggle global bypass and maintenance mode from the SPA, so that I can respond operationally without the CLI.

**Why P2**: The control surface behind the existing `NodeControlBanner` (which currently only displays state). Depends on M6 *Bypass & maintenance mode* being executed.

**Acceptance Criteria**:

1. WHEN an admin opens the node controls screen THEN it SHALL show current bypass and maintenance desired/effective state (`GET /node/health`).
2. WHEN an admin toggles bypass THEN the SPA SHALL call `POST /node/bypass`, require explicit confirmation (bypass disables scrubbing), and reflect the propagation state.
3. WHEN an admin toggles maintenance THEN the SPA SHALL call `POST /node/maintenance`, explain the queue-and-apply-on-exit behavior, and reflect state.
4. WHEN bypass or maintenance is active THEN the existing `NodeControlBanner` SHALL continue to show the active-state banner (no regression).

**Independent Test**: As admin, toggle bypass (confirm the warning), verify the banner shows "BYPASS ACTIVE", toggle it back, then toggle maintenance and read the explanation.

---

### P3: Account self-service (change password)

**User Story**: As any user, I want to change my own password from the SPA, so that I don't need an admin or the API to rotate it.

**Why P3**: Small quality-of-life addition over the existing `POST /auth/password`; not required for the config MVP.

**Acceptance Criteria**:

1. WHEN a user opens the account screen THEN it SHALL offer a change-password form calling `POST /auth/password`, validating the new password client-side and surfacing API errors.

**Independent Test**: Change the current user's password and confirm re-login works with the new password.

---

### P3: Apply-status timeline & admin job backlog

**User Story**: As an admin, I want to see the worker's apply/job backlog and a per-service apply history, so that I can diagnose stuck or failed applies.

**Why P3**: Deepens operability beyond the inline apply-status primitive; latest-state feedback (P1) satisfies the MVP.

**Acceptance Criteria**:

1. WHEN an admin opens the jobs screen THEN it SHALL show the apply/job backlog (`GET /jobs`) with per-job state and target.
2. WHEN a service's apply is `failed` THEN the service view SHALL expose the failure detail and the still-active `active_version` (`GET /services/{id}/apply-status`).

**Independent Test**: Force a failed apply (or seed one) and confirm the admin sees it in the backlog and on the service view with the last-good active version.

---

## Edge Cases

- WHEN a mutation returns `202` but the worker is down or slow THEN the apply-status primitive SHALL keep showing `queued`/`applying` (not `active`) and SHALL NOT falsely report success; a configurable poll timeout SHALL surface an "apply is taking longer than expected" state, never a fabricated `active`.
- WHEN an apply reaches `failed` THEN the UI SHALL show the failure and clearly indicate the previous `active_version` is still the live config (no data-plane change was applied).
- WHEN a tenant deep-links to a service/rule/list they do not own THEN the API returns 404/403 and the SPA SHALL render a not-found/forbidden state, never another tenant's data.
- WHEN the API rejects a write for a domain rule (CIDR ⊄ allocation, active-service overlap, >16 rules, duplicate priority, revoke-in-use, disable-first-required) THEN the SPA SHALL surface the specific API error inline, not a generic toast.
- WHEN an admin-only endpoint is called by a tenant via a crafted request THEN the API fail-closes (403) and the SPA SHALL handle it as forbidden without leaking the existence of admin data.
- WHEN a list/table is empty (no services, no rules, no feeds, no allocations) THEN the screen SHALL render a purpose-specific empty state with the correct create/allocate call-to-action.
- WHEN two admins edit the same object concurrently THEN the SPA SHALL rely on the API's `version`/optimistic response; a stale write that the API rejects SHALL surface as a conflict prompting a refresh (no silent overwrite).
- WHEN a required backend field/endpoint turns out to be missing for a screen THEN the screen SHALL be omitted and the gap recorded as a `SPEC_DEVIATION` / backend follow-up — the frontend SHALL NOT invent an endpoint.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| CFG-01 | P1: Shell & navigation | - | Pending |
| CFG-02 | P1: Shell & navigation | - | Pending |
| CFG-03 | P1: Shell & navigation | - | Pending |
| CFG-04 | P1: Shell & navigation | - | Pending |
| CFG-05 | P1: Shell & navigation | - | Pending |
| CFG-06 | P1: Shell & navigation | - | Pending |
| CFG-07 | P1: Tenant service self-service | - | Pending |
| CFG-08 | P1: Tenant service self-service | - | Pending |
| CFG-09 | P1: Tenant service self-service | - | Pending |
| CFG-10 | P1: Tenant service self-service | - | Pending |
| CFG-11 | P1: Tenant service self-service | - | Pending |
| CFG-12 | P1: Tenant service self-service | - | Pending |
| CFG-13 | P1: Tenant service self-service | - | Pending |
| CFG-14 | P1: Tenant service self-service | - | Pending |
| CFG-15 | P1: Tenant allow-rule management | - | Pending |
| CFG-16 | P1: Tenant allow-rule management | - | Pending |
| CFG-17 | P1: Tenant allow-rule management | - | Pending |
| CFG-18 | P1: Tenant allow-rule management | - | Pending |
| CFG-19 | P1: Tenant allow-rule management | - | Pending |
| CFG-20 | P1: Tenant whitelist/blacklist | - | Pending |
| CFG-21 | P1: Tenant whitelist/blacklist | - | Pending |
| CFG-22 | P1: Tenant whitelist/blacklist | - | Pending |
| CFG-23 | P1: Tenant whitelist/blacklist | - | Pending |
| CFG-24 | P1: Tenant whitelist/blacklist | - | Pending |
| CFG-25 | P2: Admin tenant & user mgmt | - | Pending |
| CFG-26 | P2: Admin tenant & user mgmt | - | Pending |
| CFG-27 | P2: Admin tenant & user mgmt | - | Pending |
| CFG-28 | P2: Admin tenant & user mgmt | - | Pending |
| CFG-29 | P2: Admin tenant & user mgmt | - | Pending |
| CFG-30 | P2: Admin CIDR allocation | - | Pending |
| CFG-31 | P2: Admin CIDR allocation | - | Pending |
| CFG-32 | P2: Admin CIDR allocation | - | Pending |
| CFG-33 | P2: Admin CIDR allocation | - | Pending |
| CFG-34 | P2: Admin service oversight & plan | - | Pending |
| CFG-35 | P2: Admin service oversight & plan | - | Pending |
| CFG-36 | P2: Admin service oversight & plan | - | Pending |
| CFG-37 | P2: Admin service oversight & plan | - | Pending |
| CFG-38 | P2: Admin feeds & global blacklist | - | Pending |
| CFG-39 | P2: Admin feeds & global blacklist | - | Pending |
| CFG-40 | P2: Admin feeds & global blacklist | - | Pending |
| CFG-41 | P2: Admin feeds & global blacklist | - | Pending |
| CFG-42 | P2: Admin feeds & global blacklist | - | Pending |
| CFG-43 | P2: Admin alerting configuration | - | Pending |
| CFG-44 | P2: Admin alerting configuration | - | Pending |
| CFG-45 | P2: Admin alerting configuration | - | Pending |
| CFG-46 | P2: Admin alerting configuration | - | Pending |
| CFG-47 | P2: Admin node bypass & maintenance | - | Pending |
| CFG-48 | P2: Admin node bypass & maintenance | - | Pending |
| CFG-49 | P2: Admin node bypass & maintenance | - | Pending |
| CFG-50 | P2: Admin node bypass & maintenance | - | Pending |
| CFG-51 | P3: Account self-service | - | Pending |
| CFG-52 | P3: Apply-status timeline & job backlog | - | Pending |
| CFG-53 | P3: Apply-status timeline & job backlog | - | Pending |

**ID format:** `CFG-[NUMBER]`

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 53 total, 0 mapped to tasks (Design/Tasks not yet started). P1 = CFG-01..24 (MVP), P2 = CFG-25..50, P3 = CFG-51..53.

---

## Success Criteria

How we know the feature is successful:

- [ ] A tenant can complete the full self-service loop — create a service, add allow-rules and whitelist/blacklist entries, edit and delete them — entirely in the browser, with each write showing its apply-status reaching `active`.
- [ ] An admin can provision a tenant end-to-end — create the tenant + user, allocate a CIDR, size a plan, add a threat feed, configure an alert channel — from the SPA.
- [ ] No mutation is presented as "done" before its apply-status reaches a terminal state; a `failed` apply is visibly distinct from `active` and shows the still-live `active_version`.
- [ ] Zero new backend endpoints were added; every screen maps to an endpoint that already exists in the control-plane API.
- [ ] Tenant isolation holds in the UI: a tenant cannot view or mutate another tenant's config, and admin-only routes are unreachable for tenants (verified in the test set).
- [ ] The existing observability, billing, and alert-history views continue to work unchanged (no regression to the M5 SPA).

---

## Roadmap note

This is a cross-cutting **frontend** feature with **no backend milestone dependency** — every API it consumes is already executed (M1 services/rules/lists + tenants/users/allocations; M4 feeds; M6 alert config + node control, once those M6 features Execute). Its P2 alerting and node-control stories **soft-depend** on M6 *Alerting* and *Bypass & maintenance mode* being executed (their config/control endpoints must exist); P1 (tenant self-service) is buildable today. Recommend adding it to ROADMAP as a standalone frontend feature (e.g. under a "Frontend / operability" track) rather than blocking any M1–M6 backend milestone.

## Open gray areas for Design

- **Async apply UX depth** — inline per-action status (P1) vs. a global apply/version indicator vs. the P3 backlog timeline; how aggressively to poll `/services/{id}/apply-status` and when to stop.
- **Form/validation approach** — reuse the existing plain-React + TanStack Query mutation pattern, or introduce a form/validation library; must stay consistent with the shipped SPA.
- **Tenant plan visibility** — confirm committed/ceiling is display-only for tenants (spec assumes yes, since `/plan` is admin-only) vs. hidden entirely.
- **Admin service create + plan-at-create** — whether the admin create form sets the plan inline (create accepts a plan) or always via the separate `/plan` PATCH.
- **Navigation IA** — one unified management area with role-filtered items vs. separate tenant/admin sections; where config sits relative to the existing dashboards.
