# Alerting Specification

**Feature:** M6 #2 — Alerting
**Context:** `.specs/features/alerting/context.md` (D-ALRT-1..4, A-ALRT-1..8)
**Status:** **Draft** (2026-07-14, awaiting approval) → Design
**Depends on (Execute-gated):**
- **Telemetry & dashboards executed** (M5, **satisfied** — VERIFIED 2026-07-14) — provides the persisted
  alert data sources (`NodeHealthSnapshot`, `TelemetryCounter`), the `TelemetryReader`, the worker
  background-lane pattern this feature's evaluator lane reuses, and the SPA shell the P2 alert panel layers on.
- **agent-worker executed** (M4 #1, **satisfied**) — the long-running worker + background-lane pattern
  (`billing`/`telemetry`/`node_control_reconciler`) the `AlertEvaluator` lane mirrors; no new Redis `JobType`.
- **Reuses (executed):** M1 auth/RBAC (`require_admin`, `load_service_for_principal`, fail-closed tenant
  ownership) + `AuditEvent` + `record_event`; apply-status `AgentJob` (apply-failed / backlog source);
  M4 #3 feed-sync `FeedSyncRun` + AD-003 whitelist-overlap audit event.
- **Soft coordination (NOT hard gates):** bypass-maintenance (M6 #1, in progress) *emits* the
  bypass/maintenance alert-worthy event this feature delivers (A-BYP-2 / A-BYP-27); until it is executed the
  bypass rule simply never fires (rule sources degrade gracefully, ALRT-09). SLA/OLA reporting (sibling M6)
  is a separate periodic-report feature, not consumed here.

---

## Problem Statement

Every operational fault the gateway can suffer — a data-plane `map_error`, XDP dropping from native to
generic, an apply/build `failed`, a feed sync failure, a stuck worker or growing backlog, a service losing
its committed clean bandwidth, an attack onset, an engaged bypass — is already *counted or recorded* by the
executed telemetry, worker, and audit surfaces, but **no one is told**. The dashboard colors these
conditions (display-only, §9.1) and sibling features emit "alert-worthy" events they explicitly defer to
this feature, yet there is no engine that turns a condition into an actual notification, tracks whether it
is still firing, and stops re-notifying. This feature adds the M6 alerting engine: a **control-plane /
worker-side evaluator** (never on the hot path) that watches the existing counters and events, opens and
auto-resolves stateful alert instances with **for-duration firing + hysteresis + dedup** to survive alert
storms, and **delivers** them over **email + generic HTTP webhook** with **severity routing** and strict
**per-tenant isolation** (a tenant is told only about their own services; node/system faults go to admin).

## Goals

- [ ] A worker-side **alert evaluator** turns the mandated §9.3 event set into notifications, reading only
      **already-persisted** telemetry/health/job/feed/node state + emitted audit events — **zero hot-path
      change, no new data-plane surface, no new counters**.
- [ ] Alerts are **stateful**: a condition that holds fires once (after a for-duration debounce), dedups
      while it persists, and **auto-resolves** (with a resolution notification) when it clears past a
      **separate lower threshold** (hysteresis band) — no alert storms, no flapping.
- [ ] Alerts are **delivered** over **email + generic webhook**, routed by **severity** and **ownership**:
      service-scoped alerts go to the **owning tenant + admin** (never another tenant, §5.2), node/system
      alerts go to **admin only**; every **critical** alert also writes an `AuditEvent`.
- [ ] An admin **configures** channels (SMTP + webhook) and may tune/enable rule thresholds (seeded from the
      §9.1 defaults); secrets are never echoed back.
- [ ] Alert **history** (active + resolved, with per-channel delivery status) is **queryable** — admin sees
      all, a tenant sees only their own services' alerts — and survives a worker/node restart.

## Out of Scope

| Feature | Reason |
| --- | --- |
| Auto-mitigation / auto-response / one-click mitigate | Alerting only *notifies*; automated response is M7 / OP-02 (PROJECT scope — v1 is manual). |
| SLA/OLA periodic **reporting** (met/missed per dimension) | Sibling M6 *SLA/OLA reporting & audit* feature — periodic reports tied to `BillingUsage`, distinct from real-time alerts. |
| New hot-path **counters / DP emission** | Alerting reads only existing persisted counters/events (A-ALRT-2); the DP/telemetry already produce them. |
| Per-tenant **self-service** channels & tenant-defined rules | v1 channels are admin-configured global (D-ALRT-3); tenant-managed channels/thresholds are deferred. |
| Native **SMS / Slack / PagerDuty** integrations | The generic HTTP webhook covers these downstream; v1 ships email + generic webhook only (A-ALRT-7). |
| Arbitrary user-defined **rule DSL / query builder** | v1 is a **fixed catalog** of §9.3 rules with tunable thresholds/enable — not an open expression engine. |
| On-call **scheduling / escalation policies** | Out of v1 scope — routing is by severity + ownership only. |
| Alert **acknowledge/silence** UI beyond the P3 minimum | Rich incident-management UX is deferred; v1 P3 adds only maintenance-window silence + manual ack. |

---

## User Stories

### P1: Alert evaluation engine & lifecycle ⭐ MVP

**User Story:** As the **operations platform**, I want a worker-side evaluator that watches the existing
counters/events and opens/auto-resolves stateful alerts with debounce, hysteresis and dedup, so a real
fault fires exactly once and clears itself — without storming operators.

**Why P1:** This is the engine; every other story (coverage, delivery, config, dashboard) is inert without a
stateful firing/resolving lifecycle.

**Acceptance Criteria:**

1. WHEN the `AlertEvaluator` background lane runs an evaluation tick THEN it SHALL read the current
   persisted telemetry/health/job/feed/node-control state (+ emitted audit events) and evaluate every
   **enabled** rule **entirely off the hot path** (worker/control-plane only, no DP call). `(ALRT-01)`
2. WHEN a rule's fire condition holds for **N consecutive** ticks THEN the system SHALL open a **firing**
   `Alert` instance keyed by a dedup key of `(rule, scope)`. `(ALRT-02)`
3. WHEN a fire condition is transient (holds fewer than N consecutive ticks) THEN the system SHALL **not**
   open an alert (for-duration flap suppression). `(ALRT-03)`
4. WHEN a firing alert's metric falls below the **separate lower clear-threshold** for **M consecutive**
   ticks THEN the system SHALL transition it to **resolved**, record `resolved_at`, and emit a resolution
   notification (hysteresis band + auto-resolve). `(ALRT-04)`
5. WHEN a condition remains firing across ticks THEN the system SHALL **not** open a duplicate alert — the
   existing `(rule, scope)` instance is updated in place (dedup). `(ALRT-05)`
6. WHEN an alert stays firing THEN re-notification SHALL be **suppressed within the configured re-notify
   window**; after the window a reminder MAY be re-sent (bounded repeat, never per-tick). `(ALRT-06)`
7. WHEN the worker/node restarts THEN open alert instances and their consecutive-tick counters SHALL be
   **recovered from persistence** — no spurious re-fire, no lost firing state, no duplicate notification
   inside the re-notify window. `(ALRT-07)`
8. WHEN a rule is disabled THEN it SHALL not be evaluated and any currently-firing alert for it SHALL be
   **auto-resolved** (no orphaned stuck-firing alerts). `(ALRT-08)`
9. WHEN the evaluator cannot read a data source (source absent — e.g. a not-yet-executed feature — or query
   error) THEN it SHALL **skip that rule for the tick** without crashing the lane or affecting other rules
   (fail-safe, bounded). `(ALRT-09)`

**Independent Test:** Drive the evaluator with synthetic source rows: assert a condition held for < N ticks
opens nothing; held ≥ N ticks opens exactly one firing alert; a second identical tick does not duplicate;
dropping the metric below the clear-threshold for M ticks resolves it; restart mid-firing recovers the
instance without a duplicate notification; removing a source skips only that rule.

---

### P1: Mandated §9.3 event coverage ⭐ MVP

**User Story:** As a **system admin / tenant**, I want every operational fault the TDD calls out to map to a
concrete alert with the right severity and audience, so nothing important is silently unmonitored.

**Why P1:** The engine is only useful if the actual §9.3 events are wired to it; this story is the rule
catalog binding each event to an existing source, severity, and scope.

**Acceptance Criteria:** (each rule reads an already-persisted source; `node` = admin scope, `service` =
owning-tenant scope)

1. WHEN `NodeHealthSnapshot.map_error_count > 0` THEN the system SHALL fire a **critical** *node* alert
   (`map_error` data-plane fault). `(ALRT-10)`
2. WHEN `xdp_mode` degrades **native → generic** (or `off`/detach) THEN the system SHALL fire a **critical**
   *node* alert. `(ALRT-11)`
3. WHEN `node_clean_bps / node_capacity_bps ≥ 0.9` (§9.1 warning ratio) THEN the system SHALL fire a
   **warning** *node* alert (near capacity / congestion); `≥ 1.0` SHALL escalate to **critical**. `(ALRT-12)`
4. WHEN an `AgentJob` reaches **`failed`** THEN the system SHALL fire a *node* alert (apply/build failed —
   §10 keeps the last-good slot, ALRT delivers the notice). `(ALRT-13)`
5. WHEN the worker stops making progress (stale heartbeat) **or** the job **backlog** exceeds its threshold
   THEN the system SHALL fire a *node* alert (worker down / backlog). `(ALRT-14)`
6. WHEN a `FeedSyncRun` **fails** (per source or all sources) THEN the system SHALL fire a *node* alert
   (feed sync failure). `(ALRT-15)`
7. WHEN a service's **committed clean throughput is not honored** (fairness breach) THEN the system SHALL
   fire a **warning** *service* SLA alert routed to the **owning tenant + admin**. `(ALRT-16)`
8. WHEN a service's drop rate crosses the **attack-onset** threshold (drop pps/bps or a drop-reason surge
   from `TelemetryCounter`) THEN the system SHALL fire a *service* alert to the **owning tenant**. `(ALRT-17)`
9. WHEN bloom false-positive volume (`bloom_hit_lpm_miss` / `bloom_stats`) exceeds its §9.1 threshold THEN
   the system SHALL fire a **warning** *node* alert. `(ALRT-18)`
10. WHEN bypass **or** maintenance becomes active (consuming the alert-worthy event emitted by
    bypass-maintenance, A-BYP-27) THEN the system SHALL fire a **critical** *node* alert while it is active
    and auto-resolve on clear. `(ALRT-19)`
11. WHEN a whitelist entry **overlaps a threat-feed** entry (AD-003 audit event, no global removal) THEN the
    system SHALL fire a **warning** alert to the **affected tenant + admin**. `(ALRT-20)`

**Independent Test:** For each rule, inject the triggering source row/event and assert the correct severity,
scope, and audience; assert node rules never route to a tenant and service rules route only to the owner.

---

### P1: Notification delivery, routing & isolation ⭐ MVP

**User Story:** As an **operator / tenant**, I want firing and resolving alerts delivered to email and
webhook, addressed only to the right audience, with a recorded delivery status, so I actually receive the
signal and can trust its confidentiality.

**Why P1:** An evaluated alert nobody receives is inert; routing + isolation is the security-critical half
(§5.2) and delivery status is what makes failures visible.

**Acceptance Criteria:**

1. WHEN an alert **fires or resolves** THEN the system SHALL deliver a notification to each **routed,
   enabled** channel (email + generic webhook). `(ALRT-21)`
2. WHEN an alert is **service-scoped** THEN it SHALL route to the **owning tenant's** contact channel **and**
   admin — and SHALL **never** be delivered to any other tenant (§5.2 isolation). `(ALRT-22)`
3. WHEN an alert is **node/system-scoped** THEN it SHALL route to **admin only** (no tenant delivery).
   `(ALRT-23)`
4. WHEN a notification is attempted THEN the system SHALL record a per-channel `AlertNotification` with
   status (`sent`/`failed`/`retrying`), attempt count, and timestamp. `(ALRT-24)`
5. WHEN a channel delivery **fails** THEN the system SHALL retry with **bounded backoff** and SHALL NOT block
   evaluation of other rules/alerts or stall the lane (best-effort, bounded, isolated failure). `(ALRT-25)`
6. WHEN a **critical** alert fires THEN the system SHALL **also** write an `AuditEvent` via `record_event`
   (§9.3 — critical alerts are audited). `(ALRT-26)`
7. WHEN a **webhook** is delivered THEN the payload SHALL be a structured JSON envelope (alert id, rule,
   severity, scope, state `firing`/`resolved`, `fired_at`, bounded context) containing **no secrets and no
   raw PII** (§9.3 logging rule). `(ALRT-27)`
8. WHEN an **email** is delivered THEN it SHALL use the admin-configured SMTP channel with no third-party
   SDK and no hot-path dependency (A-ALRT-7). `(ALRT-28)`

**Independent Test:** Fire a service-scoped alert for a service owned by tenant A and assert delivery to A +
admin and **not** to tenant B; fire a node alert and assert admin-only; point the webhook at a failing
endpoint and assert bounded retry + a `failed` `AlertNotification` while the lane keeps running; assert a
critical alert also produces an `AuditEvent`; assert the webhook JSON carries no channel secret.

---

### P1: Channel & rule configuration + alert history API ⭐ MVP

**User Story:** As a **system admin**, I want to configure delivery channels and tune rule thresholds, and I
(and tenants, for their own services) want to query alert history, so alerting is operable and auditable.

**Why P1:** Channels must be configurable for delivery to work at all, and history/query is what makes an
alert engine operationally usable and post-incident reviewable.

**Acceptance Criteria:**

1. WHEN an admin CRUDs a **notification channel** (SMTP email / generic webhook) THEN the system SHALL
   persist and validate it and require admin — a non-admin SHALL fail closed (403, no leak). `(ALRT-29)`
2. WHEN channel config contains **secrets** (SMTP password, webhook auth) THEN they SHALL be write-only —
   **never** returned in API reads, history, or logs (masked). `(ALRT-30)`
3. WHEN an admin **overrides** a rule's threshold / severity / enabled state THEN the system SHALL persist
   it; an un-overridden rule SHALL use the **§9.1-seeded default** (mirroring `theme/thresholds.ts`,
   A-ALRT-4); reading the catalog SHALL show effective values. `(ALRT-31)`
4. WHEN an admin GETs alerts THEN the system SHALL return **active + historical** alerts across the node with
   per-alert delivery status, filterable by state/severity/scope/time. `(ALRT-32)`
5. WHEN a **tenant** GETs alerts THEN the system SHALL return **only** alerts scoped to services they own
   (§5.2) — never node/system alerts nor another tenant's. `(ALRT-33)`
6. WHEN a channel or rule override is created/modified THEN the change SHALL be **audited** (dangerous-action
   for channels carrying secrets). `(ALRT-34)`

**Independent Test:** As admin, create/read/update/delete a webhook + SMTP channel and assert secrets are
masked on read; as non-admin assert 403; override a rule threshold and assert the effective catalog value
changes and an un-overridden rule shows the §9.1 default; GET alerts as admin (all) vs a tenant (own
services only) and assert isolation + filters; assert channel/rule changes are audited.

---

### P2: Alert dashboard surface *(gated on the M5 telemetry SPA shell executed)*

**User Story:** As a **dashboard user**, I want to see active and recent alerts in the SPA so I can triage
without leaving the console.

**Why P2:** The state and delivery exist via P1 API; the visual surface layers on the executed M5 SPA shell.

**Acceptance Criteria:**

1. WHEN a user opens the dashboard THEN the SPA SHALL show **active alerts** (severity-colored via §9.1) and
   a **recent-alert history**, scoped by role (admin = node + all services; tenant = own services). `(ALRT-35)`
2. WHEN alert state changes THEN the surface SHALL reflect it within the telemetry poll cadence (≤2 s),
   reusing the existing polling (no SSE/WebSocket). `(ALRT-36)`
3. WHEN an alert is shown THEN it SHALL display rule, severity, scope, state, `fired_at`/`resolved_at`, and
   delivery status. `(ALRT-37)`

**Independent Test:** With the SPA running, trigger a node and a service alert; assert the admin view shows
both and the tenant view shows only the service one, colored by severity, updating within one poll interval.

---

### P2: Severity-based channel routing & test delivery

**User Story:** As a **system admin**, I want to route severities to different channels and send a test
notification so I can tune noise and verify a channel before relying on it.

**Why P2:** Refines P1's fixed routing; useful but not required for alerts to work.

**Acceptance Criteria:**

1. WHEN a channel is configured with a **minimum severity** THEN it SHALL receive only alerts at or above it
   (e.g. webhook = info+, email = critical only). `(ALRT-38)`
2. WHEN an admin triggers a **test notification** for a channel THEN the system SHALL deliver a synthetic
   alert to that channel and report the delivery result — without creating a real `Alert` instance. `(ALRT-39)`

**Independent Test:** Set email min-severity = critical; fire a warning and assert only the webhook
receives it; send a test to each channel and assert a delivery result with no persisted alert.

---

### P3: Maintenance silence, manual ack, history export & retention

**User Story:** As a **system admin / SRE**, I want to silence expected noise during a maintenance window,
acknowledge an alert, and export/retain history, so alerting stays clean and post-incident reviewable.

**Why P3:** Operational ergonomics — the engine works without them.

**Acceptance Criteria:**

1. WHEN the node is in **maintenance mode** (M6 #1) THEN apply/swap-related alerts (e.g. `AgentJob failed`
   from a held swap) SHALL be **suppressed/silenced** for the window, while safety alerts (`map_error`,
   native→generic, bypass) SHALL still fire. `(ALRT-40)`
2. WHEN an admin **acknowledges** a firing alert THEN the system SHALL record the ack (actor, time) and
   suppress re-notification for that instance until it resolves — without changing its firing state.
   `(ALRT-41)`
3. WHEN reviewing incidents THEN alert history SHALL be **exportable** (CSV/JSON) and subject to a bounded
   **retention** prune, consistent with the telemetry/billing retention pattern. `(ALRT-42)`

**Independent Test:** Engage maintenance and assert apply-failed alerts silence while `map_error` still
fires; ack a firing alert and assert re-notification stops but state stays firing; export history and assert
a bounded row set; run the prune and assert old resolved alerts are removed.

---

## Edge Cases

- WHEN a metric hovers **inside the hysteresis band** (between clear- and fire-threshold) THEN the alert
  SHALL stay in its current state — no flap. `(→ ALRT-02, ALRT-04)`
- WHEN a fire condition repeatedly flaps just under **N** consecutive ticks THEN **no** alert SHALL open
  (for-duration absorbs it). `(→ ALRT-03)`
- WHEN 100 services breach at once THEN dedup by `(rule, scope)` + the re-notify window SHALL bound total
  notification volume (no per-tick storm). `(→ ALRT-05, ALRT-06)`
- WHEN a service-scoped condition fires for a service owned by tenant A THEN tenant B SHALL **never** receive
  it via any channel or history read. `(→ ALRT-22, ALRT-33)`
- WHEN the webhook endpoint is down/timing out THEN retries SHALL be bounded, the `AlertNotification` marked
  `failed`, and the lane SHALL keep evaluating other alerts — the alert record is **never lost**. `(→ ALRT-25)`
- WHEN the email channel is unconfigured THEN alerts SHALL still be recorded and delivered to any other
  configured channel (e.g. webhook) with no crash. `(→ ALRT-21)`
- WHEN a data source is **absent** because its feature is not yet executed (e.g. bypass before M6 #1) THEN
  that rule SHALL simply never fire — no error, other rules unaffected. `(→ ALRT-09, ALRT-19)`
- WHEN a condition **resolved while the worker was down** THEN on restart re-evaluation SHALL resolve the
  recovered alert rather than leaving it stuck-firing. `(→ ALRT-07, ALRT-08)`
- WHEN two different rules fire for the **same scope** THEN they SHALL be **distinct** alerts (distinct
  dedup keys), each with its own lifecycle. `(→ ALRT-02)`
- WHEN a generated context/`reason` string exceeds the payload bound THEN it SHALL be **safely truncated**
  in the notification (bounded), never crash delivery. `(→ ALRT-27)`
- WHEN a channel secret would appear in a webhook payload, API read, or log THEN it SHALL be omitted/masked.
  `(→ ALRT-27, ALRT-30)`

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| ALRT-01 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-02 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-03 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-04 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-05 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-06 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-07 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-08 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-09 | P1: Evaluation engine & lifecycle | Design | Pending |
| ALRT-10 | P1: §9.3 event coverage | Design | Pending |
| ALRT-11 | P1: §9.3 event coverage | Design | Pending |
| ALRT-12 | P1: §9.3 event coverage | Design | Pending |
| ALRT-13 | P1: §9.3 event coverage | Design | Pending |
| ALRT-14 | P1: §9.3 event coverage | Design | Pending |
| ALRT-15 | P1: §9.3 event coverage | Design | Pending |
| ALRT-16 | P1: §9.3 event coverage | Design | Pending |
| ALRT-17 | P1: §9.3 event coverage | Design | Pending |
| ALRT-18 | P1: §9.3 event coverage | Design | Pending |
| ALRT-19 | P1: §9.3 event coverage | Design | Pending |
| ALRT-20 | P1: §9.3 event coverage | Design | Pending |
| ALRT-21 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-22 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-23 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-24 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-25 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-26 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-27 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-28 | P1: Delivery, routing & isolation | Design | Pending |
| ALRT-29 | P1: Config & history API | Design | Pending |
| ALRT-30 | P1: Config & history API | Design | Pending |
| ALRT-31 | P1: Config & history API | Design | Pending |
| ALRT-32 | P1: Config & history API | Design | Pending |
| ALRT-33 | P1: Config & history API | Design | Pending |
| ALRT-34 | P1: Config & history API | Design | Pending |
| ALRT-35 | P2: Alert dashboard surface | - | Pending |
| ALRT-36 | P2: Alert dashboard surface | - | Pending |
| ALRT-37 | P2: Alert dashboard surface | - | Pending |
| ALRT-38 | P2: Severity routing & test delivery | - | Pending |
| ALRT-39 | P2: Severity routing & test delivery | - | Pending |
| ALRT-40 | P3: Silence / ack / export / retention | - | Pending |
| ALRT-41 | P3: Silence / ack / export / retention | - | Pending |
| ALRT-42 | P3: Silence / ack / export / retention | - | Pending |

**ID format:** `ALRT-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 42 total, 0 mapped to Design/Tasks yet (spec draft). **P1 = ALRT-01..34** (engine 01–09,
coverage 10–20, delivery 21–28, config/history 29–34), **P2 = ALRT-35..39**, **P3 = ALRT-40..42**.

---

## Success Criteria

- [ ] An admin configures an email + webhook channel; a simulated `map_error > 0` fires **one** critical
      node alert delivered to both within one evaluation tick + delivery, and **auto-resolves** (with a
      resolution notification) when `map_error` returns to 0 — no duplicate notifications in between.
- [ ] A flapping / borderline metric never produces an alert storm: the for-duration debounce + hysteresis
      band + re-notify window bound notifications; a condition under N ticks opens nothing.
- [ ] Every §9.3 mandated event (map_error, native→generic, near-capacity, apply-failed, worker/backlog,
      feed-fail, committed breach, attack onset, bloom-FP, bypass/maintenance, whitelist-overlap) maps to an
      enabled rule that fires in a synthetic scenario with the correct severity and audience.
- [ ] A service-scoped alert is delivered to the owning tenant + admin and to **no other tenant**; node
      alerts reach admin only; each **critical** alert also appears in the audit log.
- [ ] Alerts + per-channel delivery status are queryable (admin = all, tenant = own services), survive a
      worker/node restart, and channel secrets are never exposed on read.
- [ ] **Zero hot-path change** — the whole feature runs in the worker/control-plane against existing
      persisted sources; the DP verdict pipeline and its counters are untouched.
