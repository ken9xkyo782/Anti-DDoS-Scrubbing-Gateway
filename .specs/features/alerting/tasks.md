# Alerting Tasks

**Design**: `.specs/features/alerting/design.md` (AD-033)
**Spec**: `.specs/features/alerting/spec.md` (ALRT-01..42)
**Status**: Draft (2026-07-14, awaiting approval → Execute)

> **Track:** single **control-plane / worker** track — **zero data-plane work** (alerting reads only
> already-persisted rows; no new DP surface or counter). Per `.specs/codebase/TESTING.md`, only **unit**
> tasks may be `[P]`; every **integration** task serializes on the shared `compose.test.yml` Postgres+Redis,
> so even code-independent integration branches (the T6→T7 lane chain vs the T8→T9 API chain) still run
> one-at-a-time.
>
> **Execute gate (hard):** *Telemetry & dashboards* must be **executed** (satisfied — VERIFIED 2026-07-14):
> alerting reads its `NodeHealthSnapshot` / `TelemetryCounter` rows and reuses `_committed_clean_bps`. The
> whole slice below is buildable/testable now against **fixture source rows** (no data-plane needed). **P2
> SPA (T10) additionally gated on the telemetry frontend shell executed.** `NodeControl` (M6 #1) already
> exists at migration `_0010`, so the bypass/maintenance rule (ALRT-19) + maintenance-silence gate (ALRT-40)
> work today; any absent source is skipped fail-safe (ALRT-09).
>
> **Baselines pinned live at Execute:** control-plane `B_cp = pytest -q` total on the current head
> (≥ 507 after telemetry+billing; +node_control). Frontend `B_fe` = the telemetry SPA Vitest total (≥ 34).
> Each task states the tests it **adds**; cite the new live total in its Done-when.

---

## Execution Plan

### Phase 1 — Foundation (T2 unit `[P]`; T1/T3 integration, serial)

```
T1 (int)     T2 [P] (unit)     T3 (int)
```

### Phase 2 — Source reader (integration)

```
T1 ─→ T4
```

### Phase 3 — Lifecycle engine (integration)

```
T2, T3, T4 ─→ T5
```

### Phase 4 — Delivery + history read (two code-independent integration branches, serialized on infra)

```
T3, T5 ─→ T6            (dispatcher/channels)
T3 ─────→ T8            (history read router)
```

### Phase 5 — Worker wiring + admin config (integration)

```
T5, T6 ─→ T7            (worker lane wiring + settings + retention prune)
T3, T6, T8 ─→ T9        (rules/channels config + test-send)
```

### Phase 6 — P2 SPA / P3 endpoints / docs

```
T8 ─→ T10 [P fe]  (gated: telemetry FE)
T8, T9 ─→ T11 (int, P3)
T7, T9 ─→ T12 [P] (docs)
```

---

## Task Breakdown

### T1: `services/telemetry_math.py` — extract committed-bps helper

**What**: Extract `_committed_clean_bps(plan)` (+ a `committed_honored(bps, plan)` predicate) out of the
telemetry router into a shared pure module and re-point the router at it (import-only, behavior-identical) so
the fairness/SLA rule reuses one implementation (no logic fork).
**Where**: `control-plane/app/services/telemetry_math.py` (new), `app/api/routers/telemetry.py` (re-import)
(+ `tests/unit/test_telemetry_math.py`)
**Depends on**: None
**Reuses**: the existing `_committed_clean_bps` body (`api/routers/telemetry.py` L460) verbatim; `ServicePlan`
(`committed_clean_gbps` gigabits → bits/s ×1e9), `Numeric`/`Decimal` conventions.
**Requirement**: ALRT-16 (foundation), D-033 flag 3

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `committed_clean_bps(plan: ServicePlan | None) -> int` returns the exact value the router computed;
      `committed_honored(bps: int, plan: ServicePlan | None) -> bool | None` = `bps >= committed_clean_bps` (`None` when no plan) — the telemetry router's L453/L536 call sites use them.
- [ ] `api/routers/telemetry.py` imports both from the new module; **no behavior change** (the local copy is removed, not duplicated).
- [ ] Unit tests cover: `None` plan → `0`/`None`; a Gbps→bits/s conversion case (e.g. `10 Gbps → 10_000_000_000 bits/s`); honored true/false boundary.
- [ ] Existing telemetry API integration tests still pass unchanged (regression proof).
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~3 new pass (cite live); telemetry API tests unchanged.

**Tests**: integration (touches the router layer; helper unit-tested within) · **Gate**: full
**Commit**: `refactor(telemetry): extract committed-bps helper for reuse by alerting`

---

### T2: `services/alert_rules.py` — §9.3 rule catalog + pure predicates [P]

**What**: The fixed rule catalog (one `RuleDef` per §9.3 event, ALRT-10..20) with §9.1-seeded default
thresholds, and the **pure** `evaluate(inputs, effective) -> list[RuleObservation]` that maps an `AlertInputs`
snapshot to per-(rule,scope) firing observations.
**Where**: `control-plane/app/services/alert_rules.py` (+ `tests/unit/test_alert_rules.py`)
**Depends on**: None
**Reuses**: `Severity` semantics + threshold values mirrored from `frontend/src/theme/thresholds.ts` (§9.1);
operates only on `AlertInputs` fields (incl. pre-computed `committed_bps`) — **no I/O, no router/helper import**.
**Requirement**: ALRT-10..20, ALRT-31 (effective = override ?? default), D-033-1/D-033-6

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `RULES` declares the 11 catalog entries with `key`/`scope`/default `severity`/`fire`+`clear` thresholds/`default_enabled`/`silence_in_maintenance`; §9.1 mirror asserted (map_error>0 crit; clean/cap 0.9 warn/1.0 crit; committed-not-honored warn; bloom_fp≥1000 warn) + new defaults (attack-onset drop-share, backlog, stuck-applying, telemetry-stale, feed-fail, apply-failed, bypass/maintenance).
- [ ] `evaluate(inputs, effective)` returns one `RuleObservation(rule_key, scope_key, tenant_id, service_id, severity, metric_value, firing, context)` per in-scope subject; **disabled rules produce nothing**; escalating near-capacity yields warn vs crit by band.
- [ ] Pure — deterministic given `AlertInputs`; no DB/network.
- [ ] Unit tests (table-driven): each rule fires at/above its fire threshold and not below clear; near-capacity warn/crit escalation; disabled rule silent; service rules carry tenant/service, node rules carry neither.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`
- [ ] Test count: `B_cp` unit + ~14 new pass (cite live).

**Tests**: unit · **Gate**: quick
**Commit**: `feat(alerting): add the §9.3 rule catalog and pure evaluation predicates`

---

### T3: alert models + enums + migration `_0011`

**What**: The four additive models (`AlertRule`, `NotificationChannel`, `Alert`, `AlertNotification`) + the
five enums, and their Alembic migration.
**Where**: `control-plane/app/db/models.py`, `control-plane/migrations/versions/20260714_0011_alerting.py`
(+ `tests/integration/test_alert_models.py`)
**Depends on**: None
**Reuses**: `TimestampMixin`, `SAEnum(native_enum=False, values_callable=…)`, `JSONB`, `Numeric(18,4)`,
`UUID(as_uuid=True)` PK, FK `ondelete` (`SET NULL` durable / `CASCADE` transient), partial-unique `Index(…,
postgresql_where=…)` idiom; migration head = `20260714_0010_node_control` (down_revision pinned live).
**Requirement**: ALRT-02/05 (dedup index), ALRT-07 (in-row streaks), ALRT-30 (secret column), ALRT-33
(denormalized tenant_id), ALRT-41 (ack fields), D-033-2/D-033-3/D-033-8

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `AlertScope{node,service}`, `AlertState{pending,firing,resolved}`, `AlertSeverity{info,warning,critical}`, `ChannelKind{email,webhook}`, `NotificationState{pending,sent,retrying,failed}` all `native_enum=False`.
- [ ] `AlertRule(key UNIQUE, enabled, severity_override?, fire/clear_threshold_override?, silence_in_maintenance)`.
- [ ] `NotificationChannel(name, kind, tenant_id FK CASCADE nullable, enabled, min_severity, config JSONB, secret? write-only)` + `Index(tenant_id, enabled)`.
- [ ] `Alert(rule_key, scope, scope_key, service_id FK SET NULL?, tenant_id FK SET NULL?, service_name?, severity, state, metric_value?, context JSONB, fire_streak, clear_streak, first_observed_at, fired_at?, resolved_at?, last_notified_at?, acknowledged_at?, acknowledged_by FK SET NULL?)` with a **partial-unique** index on `(rule_key, scope_key) WHERE state <> 'resolved'` + `(tenant_id, state)` + `(state, fired_at)`.
- [ ] `AlertNotification(alert_id FK CASCADE, channel_id FK SET NULL?, channel_name, kind, trigger, state, attempts, last_error?, sent_at?)` + `Index(alert_id)`.
- [ ] Migration `upgrade`/`downgrade` reversible; `alembic upgrade head` applies clean on the test DB.
- [ ] Integration tests assert: partial-unique dedup (two non-resolved same-key rows collide; a resolved one frees the key); FK `SET NULL` (alert survives service/tenant/channel delete, name snapshots readable); `AlertNotification` CASCADE with its alert; enum round-trips; `secret` never required.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`; `alembic upgrade head` on test DB.
- [ ] Test count: `B_cp` + ~9 new pass (cite live).

**Tests**: integration · **Gate**: full (+ build: `alembic upgrade head`)
**Commit**: `feat(alerting): add alert rule/channel/instance/notification models and migration`

---

### T4: `worker/alert_sources.py` — `AlertSources.load` → `AlertInputs`

**What**: One batched read of the persisted source rows into an immutable `AlertInputs` snapshot (latest
health, recent telemetry per service/node, job backlog/failed/stuck, feed failures + overlaps, node-control),
pre-computing `committed_bps` per service and telemetry staleness.
**Where**: `control-plane/app/worker/alert_sources.py` (+ `tests/integration/test_alert_sources.py`)
**Depends on**: T1
**Reuses**: `NodeHealthSnapshot`/`TelemetryCounter`/`AgentJob`/`FeedSyncRun`+`FeedSyncOverlap`/`NodeControl`
models; `ProtectedService.dp_id → (service, tenant)` + `ServicePlan` join (billing/telemetry reader shape);
`telemetry_math.committed_clean_bps` (T1); `session_scope`.
**Requirement**: ALRT-01, ALRT-09 (partial/absent source), sources for ALRT-10..20

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `load(db, now) -> AlertInputs` returns a frozen dataclass: latest `NodeHealthSnapshot` (+ `age = now − captured_at` staleness), recent service+node `TelemetryCounter` mapped to `(service, tenant, committed_bps)`, `AgentJob` queued-count / newest-failed / oldest-applying, recent `FeedSyncRun` failures + open overlaps→service/tenant, the `NodeControl` singleton.
- [ ] A missing source (e.g. no health row yet, no `NodeControl`) yields `None`/empty on that field without raising (feeds ALRT-09 skip).
- [ ] Integration tests: populated snapshot maps every field; empty DB → all-empty inputs, no crash; a service `dp_id` with a plan surfaces `committed_bps`; stale health age computed.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~6 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(alerting): read persisted sources into an AlertInputs snapshot`

---

### T5: `worker/alert_evaluator.py` — lifecycle engine + `run_loop`

**What**: The `AlertEvaluator` lane: `reconcile(observations)` driving the `pending→firing→resolved`
lifecycle (for-duration debounce, hysteresis band, dedup, re-notify window, auto-resolve, disabled/absent
auto-resolve, maintenance-silence gate, critical→audit), plus `tick()`/`run_loop(stop)` and the injected
`NotificationDispatcher` seam.
**Where**: `control-plane/app/worker/alert_evaluator.py` (+ `tests/integration/test_alert_evaluator.py`)
**Depends on**: T2, T3, T4
**Reuses**: `alert_rules.evaluate` (T2), `AlertSources` (T4), `Alert`/`AlertRule` models (T3), telemetry
`run_loop` catch-log-continue shape, `services/audit.record_event` (critical→audit), `session_scope`;
inject a **recording dispatcher double** in tests (RecordingApplier precedent).
**Requirement**: ALRT-01..09, ALRT-26 (critical→audit), ALRT-40 (maintenance silence)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `reconcile`: keyed by `(rule_key, scope_key)` over non-resolved alerts — firing+no-alert → `pending(fire_streak=1)`; firing+pending reaching **N** (`worker_alert_fire_ticks`) → `firing`+enqueue fire+`fired_at`; firing+firing → dedup (update metric) + reminder only past `renotify_seconds` and not acked; not-firing+pending → reset (silent); below clear-threshold+firing reaching **M** (`worker_alert_clear_ticks`) → `resolved`+enqueue resolve; band → hold; disabled/absent → auto-resolve.
- [ ] `firing` critical transitions call `record_event(actor=None, action="alert.fired", target_type="alert", outcome="critical", metadata=scrubbed)`.
- [ ] Maintenance (`NodeControl.maintenance_enabled`) suppresses **enqueue** for `silence_in_maintenance` rules (alert still opens, no delivery); safety rules still enqueue (ALRT-40).
- [ ] `run_loop(stop)`: `tick()` (load→evaluate→reconcile→dispatch_pending, own `session_scope`) then interruptible sleep `worker_alert_interval_seconds`; catch-log-continue (one bad tick never stops the lane, ALRT-09).
- [ ] **Restart recovery**: with pre-seeded `Alert` rows, a fresh evaluator resumes streaks/`last_notified_at` from the DB — no duplicate fire inside the re-notify window (ALRT-07).
- [ ] Integration tests (recording dispatcher): sub-N transient opens nothing; N-tick fire opens one + enqueues fire; dedup no-duplicate; band holds; M-tick auto-resolve enqueues resolve; disabled auto-resolves; critical writes an `AuditEvent`; maintenance silences the gated rule but not `map_error`; restart resumes without re-fire; a raising tick is swallowed.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~12 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(alerting): add the stateful alert evaluator lane with debounce and hysteresis`

---

### T6: `worker/alert_dispatch.py` — dispatcher, channels, routing & isolation

**What**: `NotificationDispatcher` — channel **selection/routing** (by `tenant_id` scope + `min_severity`),
`AlertNotification` creation + bounded-retry delivery + status, the `EmailChannel` (stdlib `smtplib` via
`to_thread`) and `WebhookChannel` (httpx JSON envelope, no secrets/PII), and `send_test`.
**Where**: `control-plane/app/worker/alert_dispatch.py` (+ `tests/integration/test_alert_dispatch.py`)
**Depends on**: T3, T5
**Reuses**: `NotificationChannel`/`AlertNotification`/`Alert` (T3), the dispatcher Protocol seam (T5),
`httpx.AsyncClient` (feed-runner lifecycle), stdlib `smtplib`, `services/audit.scrub_metadata` (envelope
hygiene); `httpx.MockTransport` + a fake SMTP sink in tests (feed-fetch test precedent).
**Requirement**: ALRT-21..28 (delivery/routing/isolation/critical-audit hooks already in T5), ALRT-38
(min_severity), ALRT-39 (`send_test`)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `select_channels(alert)`: enabled channels where `min_severity ≤ severity` **and** (`node` → `tenant_id IS NULL`) or (`service` → `tenant_id == alert.tenant_id` OR `tenant_id IS NULL`). A tenant channel never matches a node alert or another tenant's alert (**isolation is structural**, ALRT-22/23).
- [ ] Per selected channel, create an `AlertNotification` and deliver: webhook = httpx POST of the bounded JSON envelope (`alert_id, rule, severity, scope, service_id?, tenant_id?, state, fired_at, resolved_at?, metric, title, context`) — **no secret, no raw PII**, context truncated (ALRT-27); email = MIME via `asyncio.to_thread(smtplib…)` using channel SMTP config + write-only `secret` (ALRT-28).
- [ ] Failure → `state=failed`/`retrying`, `attempts` bounded by `worker_alert_max_attempts` with backoff; **per-channel isolated** — one failure never blocks another channel or the lane (ALRT-24/25).
- [ ] `send_test(channel)` delivers a synthetic alert and returns the result **without** persisting an `Alert` (ALRT-39).
- [ ] Integration tests: node alert → only NULL-scope channels; service alert (owner A) → A's + NULL channels, **not** tenant B's; below-min-severity channel skipped; webhook `MockTransport` success + 5xx→retry→failed with lane continuing; email sink receives a secret-free message; envelope carries no channel secret; `send_test` persists no `Alert`.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~10 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(alerting): deliver alerts to email/webhook channels with scoped routing`

---

### T7: worker wiring + `worker_alert_*` settings + history retention prune

**What**: Run the `AlertEvaluator` lane in the worker process, add the `worker_alert_*` settings, and prune
resolved-alert history beyond the retention horizon.
**Where**: `app/worker/worker.py`, `app/worker/__main__.py`, `app/core/config.py`,
`app/worker/alert_evaluator.py` (prune) (+ extend `tests/integration/test_worker_runtime.py`)
**Depends on**: T5, T6
**Reuses**: the `Worker.run` spawn/await/cancel lane lifecycle (`_finish_background_lane`; telemetry/billing/
node_control precedent), the `AlertLane` Protocol shape, `worker_*` settings convention, `session_scope`.
**Requirement**: A-ALRT-1 (lane, no JobType), ALRT-42 (retention prune)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `Worker.__init__(alerts=None)` + `AlertLane` Protocol; `run()` spawns `alerts.run_loop(stop_event)` before the loop and drains it in `finally` via `_finish_background_lane` alongside the other lanes; `__main__` builds `AlertSources`+`AlertEvaluator`+`NotificationDispatcher` and injects when `worker_alert_enabled`.
- [ ] `Settings`: `worker_alert_enabled=True`, `worker_alert_interval_seconds=Field(15.0, gt=0)`, `worker_alert_fire_ticks=Field(2, ge=1)`, `worker_alert_clear_ticks=Field(2, ge=1)`, `worker_alert_renotify_seconds=Field(1800.0, gt=0)`, `worker_alert_max_attempts=Field(3, ge=1)`, `worker_alert_delivery_timeout_seconds=Field(10.0, gt=0)`, `worker_alert_history_retention_days=Field(90, gt=0)`, `worker_alert_backlog_threshold`, `worker_alert_stuck_applying_seconds`, `worker_alert_telemetry_stale_seconds`.
- [ ] `prune_history()` deletes `resolved` alerts older than `worker_alert_history_retention_days` (cascading their notifications); called from the tick.
- [ ] Integration tests: worker spawns/cancels the alert lane cleanly on stop; the lane appears in the startup log flags; prune removes only old resolved alerts.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~5 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(alerting): run the alert lane in the worker with settings and retention`

---

### T8: `/alerts` history read router + schemas

**What**: The `GET /alerts` (admin all · tenant own-service, filters, empty state) and `GET /alerts/{id}`
(with notification rows) endpoints + response schemas, with strict tenant isolation.
**Where**: `app/api/routers/alerts.py` (new), `app/api/schemas/alerts.py` (new), `app/api/main.py` (register)
(+ `tests/integration/test_alerts_api.py`)
**Depends on**: T3
**Reuses**: `core/deps.py` (`get_current_user`, `require_admin`, `load_service_for_principal`→404,
`scope_to_tenant`, `Principal.tenant_id`), `routers/telemetry.py` router/schema `Annotated[..., Depends]`
conventions; `Alert`/`AlertNotification` models (T3).
**Requirement**: ALRT-32, ALRT-33 (+ ALRT-24 delivery status surfaced)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `GET /alerts?state=&severity=&scope=&service_id=&since=`: admin → all alerts + delivery summary; tenant → only `scope=service AND tenant_id == principal.tenant_id` (node/other-tenant hidden); `service_id` → `load_service_for_principal` first (404 cross-tenant); empty → `200 {alerts:[], has_data:false}`.
- [ ] `GET /alerts/{id}`: same isolation; includes `AlertNotificationResponse[]`; cross-tenant/node → 404 for a tenant.
- [ ] Read-only; secrets never present (alerts carry no channel secret).
- [ ] Integration tests (AsyncClient): admin sees node+service; tenant sees only own service; tenant cannot read a node alert or tenant B's alert (404/hidden); filters; empty state; `{id}` includes notifications.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~8 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(alerting): add tenant-scoped alert history read endpoints`

---

### T9: `/alerts/rules` + `/alerts/channels` admin config + test-send

**What**: Admin config surface on the same router: rule catalog read + threshold/severity/enabled override;
`NotificationChannel` CRUD (secret write-only, audited); `POST /alerts/channels/{id}/test`.
**Where**: `app/api/routers/alerts.py` (modify), `app/api/schemas/alerts.py` (modify)
(+ extend `tests/integration/test_alerts_api.py`)
**Depends on**: T3, T6, T8
**Reuses**: `require_admin` (403), `services/audit.record_event` (dangerous-action) + `scrub_metadata`,
`alert_rules.RULES` effective-value merge (T2), the dispatcher `send_test` (T6); T8 router/schema scaffolding.
**Requirement**: ALRT-29, ALRT-30, ALRT-31, ALRT-34, ALRT-39

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `GET /alerts/rules` returns the catalog with **effective** threshold/severity/enabled (override ?? §9.1 default); `PATCH /alerts/rules/{key}` persists an override, audited.
- [ ] `GET/POST/PATCH/DELETE /alerts/channels`: `require_admin`; secrets are **write-only** — never serialized on read (ALRT-30); every mutation audited as a dangerous action (ALRT-34); validation (email vs webhook config shape).
- [ ] `POST /alerts/channels/{id}/test`: `require_admin`; delivers a synthetic alert via the dispatcher and returns the delivery result; **no `Alert` persisted** (ALRT-39).
- [ ] Non-admin → 403 on every config route (fail-closed, ALRT-29).
- [ ] Integration tests: rule effective/override round-trip; channel CRUD with secret masked on read; non-admin 403; audit rows written; test-send returns a result without an `Alert`; a scrubbed secret never appears in the audit metadata.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~10 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(alerting): add admin rule/channel config and channel test-send`

---

### T10: Alerts panel in the SPA (P2 — gated on telemetry frontend)

**What**: An `AlertsPanel` view/route showing active + recent alerts, role-scoped and severity-colored,
reusing the telemetry SPA shell.
**Where**: `control-plane/frontend/` — new `AlertsPanel` + route + `useAlerts` query (+ Vitest tests)
**Depends on**: T8 (+ **telemetry SPA shell executed**)
**Reuses**: telemetry SPA shell (`/auth/me` role routing, `AppLayout`, TanStack Query `refetchInterval` 2 s,
`api/client.ts`, `theme/thresholds.ts` `severityColor`), D-TEL-2.
**Requirement**: ALRT-35, ALRT-36, ALRT-37

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] Admin view: node + all service alerts; tenant view: own-service alerts only; each row shows rule, severity (colored), scope, state, `fired_at`/`resolved_at`, delivery status; updates within the 2 s poll.
- [ ] Tenant never sees another tenant's / a node alert (API-enforced; UI honors it).
- [ ] Vitest component tests for the panel states (loading/empty/active/resolved, admin vs tenant scope, severity coloring).
- [ ] Gate check passes: `cd control-plane/frontend && npm run lint && npm run typecheck && npm run test -- --run && npm run build`
- [ ] Test count: `B_fe` + new pass (cite live).

**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(alerting): add the alerts panel to the SPA`

---

### T11: P3 endpoints — acknowledge + history export

**What**: `POST /alerts/{id}/ack` (suppress re-notify until resolve, keep firing state) and
`GET /alerts/export?format=csv|json&since=` (admin history export).
**Where**: `app/api/routers/alerts.py` (modify), `app/api/schemas/alerts.py` (modify)
(+ extend `tests/integration/test_alerts_api.py`)
**Depends on**: T8, T9
**Reuses**: T8/T9 router/schemas + isolation; `Alert.acknowledged_*` (T3); `StreamingResponse` for CSV
(billing export precedent); `record_event` for the ack audit.
**Requirement**: ALRT-41, ALRT-42

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `POST /alerts/{id}/ack`: records `acknowledged_at`/`acknowledged_by`, suppresses reminders for that instance until it resolves, **without** changing `state` (still `firing`); admin any, tenant own-service (404 cross-tenant); audited.
- [ ] `GET /alerts/export`: `require_admin`; CSV via `StreamingResponse` + JSON; columns rule/severity/scope/service/tenant/state/fired_at/resolved_at/delivery-summary; no secrets.
- [ ] Integration tests: ack stops the reminder but keeps `firing`; ack isolation (tenant own-only); export CSV + JSON shape; non-admin export 403.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `B_cp` + ~5 new pass (cite live).

**Tests**: integration · **Gate**: full
**Commit**: `feat(alerting): add alert acknowledge and history export endpoints`

---

### T12: Docs — TESTING.md alert layers + README/worker notes [P]

**What**: Document the alert worker lane, the new test layers, the routing/isolation + anti-storm
conventions, and the `/alerts` surface.
**Where**: `.specs/codebase/TESTING.md`, control-plane `README`/worker docs
**Depends on**: T7, T9
**Reuses**: existing TESTING.md structure (Coverage Matrix rows, conventions section).
**Requirement**: cross-cutting (documents ALRT-01..42)

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:
- [ ] TESTING.md Coverage Matrix gains `app/services/alert_rules.py` + `app/services/telemetry_math.py` (unit), `app/worker/alert_sources.py` / `alert_evaluator.py` / `alert_dispatch.py` (integration), `app/api/routers/alerts.py` (integration).
- [ ] An "Alerting conventions" note records: the `pending→firing→resolved` lifecycle with for-duration + hysteresis + dedup + auto-resolve, channel-scope routing/isolation (`tenant_id` NULL = node/admin), stdlib-SMTP + httpx delivery with a recording dispatcher / `MockTransport` in tests, and the §9.1 threshold mirror.
- [ ] Gate check passes: `python -c "import app.main"` (import smoke — confirms no code drift).
- [ ] No test count change (docs only).

**Tests**: none · **Gate**: build
**Commit**: `docs(alerting): document alert test layers and evaluator/routing conventions`

---

## Pre-Approval Validation (all three checks)

### Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 telemetry_math extraction | 1 pure module + 1 import re-point (cohesive) | ✅ Cohesive |
| T2 alert_rules | 1 pure module (catalog + `evaluate`) | ✅ Granular |
| T3 models + migration | 4 cohesive additive models + 5 enums + 1 migration (one schema unit) | ✅ Cohesive |
| T4 AlertSources | 1 reader module | ✅ Granular |
| T5 evaluator lifecycle + run_loop | 1 concern (the lifecycle engine + its loop + dispatcher seam) | ✅ Cohesive |
| T6 dispatcher + channels | 1 concern (delivery: routing + 2 channel impls) | ✅ Cohesive |
| T7 worker wiring + settings + prune | 1 concern (run the lane in the worker) | ✅ Cohesive |
| T8 history read router | 1 router (2 read endpoints, same file) | ✅ Cohesive |
| T9 config router | 1 router surface (rules + channels + test, same file) | ✅ Cohesive |
| T10 SPA panel | 1 view + query | ✅ Granular |
| T11 P3 endpoints | 2 small endpoints (ack + export, same file) | ✅ Cohesive |
| T12 docs | doc edits | ✅ Granular |

### Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram arrows in | Status |
| --- | --- | --- | --- |
| T1 | None | (Phase 1 root) | ✅ |
| T2 | None | (Phase 1 root) | ✅ |
| T3 | None | (Phase 1 root) | ✅ |
| T4 | T1 | T1→T4 | ✅ |
| T5 | T2, T3, T4 | T2→T5, T3→T5, T4→T5 | ✅ |
| T6 | T3, T5 | T3→T6, T5→T6 | ✅ |
| T7 | T5, T6 | T5→T7, T6→T7 | ✅ |
| T8 | T3 | T3→T8 | ✅ |
| T9 | T3, T6, T8 | T3→T9, T6→T9, T8→T9 | ✅ |
| T10 | T8 (+telemetry FE) | T8→T10 | ✅ |
| T11 | T8, T9 | T8→T11, T9→T11 | ✅ |
| T12 | T7, T9 | T7→T12, T9→T12 | ✅ |

No `[P]` task depends on another `[P]` task in its phase (T2⊥T1/T3; T10 fe ⊥ T12 docs in Phase 6). ✅

### Check 3 — Test Co-location (vs TESTING.md Coverage Matrix)

| Task | Code layer | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | `services/telemetry_math.py` (unit) **+** `api/routers/telemetry.py` (integration) | integration (highest) | integration | ✅ |
| T2 | `services/alert_rules.py` (pure logic) | unit | unit | ✅ |
| T3 | `db/models.py` + migration | integration | integration | ✅ |
| T4 | `worker/alert_sources.py` (DB reader) | integration | integration | ✅ |
| T5 | `worker/alert_evaluator.py` (DB/lane) | integration | integration | ✅ |
| T6 | `worker/alert_dispatch.py` (DB/httpx/smtp) | integration | integration | ✅ |
| T7 | `worker/{worker,__main__}.py` + config | integration (highest) | integration | ✅ |
| T8 | `api/routers/alerts.py` | integration | integration | ✅ |
| T9 | `api/routers/alerts.py` + audit reuse | integration | integration | ✅ |
| T10 | `frontend/` (fe layer) | fe-unit | fe | ✅ |
| T11 | `api/routers/alerts.py` | integration | integration | ✅ |
| T12 | docs (TESTING.md/README) | none | none | ✅ |

All three checks pass — no ❌.

**Parallelism note:** only **T2** (unit) and **T12** (none) + **T10** (separate `fe` toolchain) are `[P]`.
T1/T3–T9/T11 are integration → they **serialize** on `compose.test.yml` even where code-independent (the
T6→T7 lane chain and the T8→T9→T11 API chain don't share code but still run one-at-a-time). T10 is a separate
frontend toolchain, gated on the telemetry SPA shell.

---

## Tools per task (precedent: `coding-guidelines` for code, `docs-writer` for docs)

No MCPs (Context7 recorded unavailable in prior sessions; nothing here needs external API lookup — all
grounded in-repo + approved AD-033, stdlib `smtplib` + existing `httpx`). All code tasks use the
`coding-guidelines` skill; T12 uses `docs-writer`. Override this assignment if you prefer different tooling.

---

## Requirement Coverage

| Requirement | Task(s) |
| --- | --- |
| ALRT-01..09 (engine & lifecycle) | T5 (+ T2 rules, T3 model/dedup, T4 sources, T7 restart/run) |
| ALRT-10..20 (§9.3 event coverage) | T2 (catalog + predicates), T4 (sources), T1 (committed math for ALRT-16) |
| ALRT-21..28 (delivery, routing, isolation) | T6 (+ T3 models, T5 critical→audit for ALRT-26) |
| ALRT-29..31, ALRT-34 (config) | T9 (+ T2 effective defaults) |
| ALRT-32..33 (history read) | T8 |
| ALRT-35..37 (SPA surface, P2) | T10 |
| ALRT-38 (severity routing) / ALRT-39 (test-send) | T6 (38) / T9 (39) |
| ALRT-40 (maintenance silence) | T5 |
| ALRT-41 (ack) / ALRT-42 (export + retention) | T11 (ack, export) + T7 (retention prune) |

All 42 requirements mapped. **Next: approve → Execute** (Phase 1: T2 `[P]` alongside T1 then T3; then
T4 → T5; then the T6→T7 lane chain and T8→T9→T11 API chain serialized on infra; T10 when the telemetry SPA
is up; T12 `[P]` at the end).
