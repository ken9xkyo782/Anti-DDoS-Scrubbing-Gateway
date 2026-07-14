# Alerting Context

**Gathered:** 2026-07-14
**Spec:** `.specs/features/alerting/spec.md` (ALRT-01..42)
**Status:** Ready for design

---

## Feature Boundary

M6 #2 — **Alerting**: a control-plane / worker-side engine that turns the mandated §9.3 operational events
(already counted/recorded by executed telemetry, worker, apply, feed, node-control and audit surfaces) into
**delivered notifications** over **email + generic HTTP webhook**, with **≥3 severities** (info / warning /
critical), **for-duration + hysteresis + dedup + auto-resolve** anti-storm handling, strict **per-tenant
isolation** (service alerts → owning tenant + admin; node/system → admin only), and a **queryable history**.
It reads **only already-persisted data** and runs **entirely off the hot path** — it adds no data-plane
surface and no new counters. Delivery of the alert-worthy events emitted by sibling features (bypass/
maintenance, feed whitelist-overlap, apply-failed) is exactly this feature's job.

---

## Implementation Decisions

### D-ALRT-1 — Evaluation architecture: a new dedicated worker lane

- Alert conditions are evaluated by a **new `AlertEvaluator` background asyncio lane** in the worker,
  mirroring the executed `billing` / `telemetry` / `node_control_reconciler` lanes — its **own tick**, **no
  new Redis `JobType`**, no ledger writes on the hot path.
- Each tick reads the current **persisted** source rows — `NodeHealthSnapshot`, `TelemetryCounter`,
  `AgentJob`, `FeedSyncRun`, `NodeControl` — plus emitted `AuditEvent` records, evaluates every enabled
  rule, and drives the alert lifecycle. Counter-derived alerts (attack onset, near-capacity, bloom-FP,
  fairness breach) therefore **exist** in v1 (they are not event-only).
- Cadence is independent of the telemetry lane (it is *not* piggybacked) so a slow delivery can never stall
  telemetry rollups; a sensible default tick is on the order of the telemetry/health cadence (Design fixes
  the exact interval + settings knob).

### D-ALRT-2 — Alert model: stateful lifecycle tables

- Persist a **stateful** model (Design fixes exact columns/migration):
  - **`AlertRule`** — the fixed §9.3 catalog: source binding, fire/clear thresholds, severity, scope
    (`node`/`service`), enabled, optional admin override.
  - **`Alert`** — an **instance** keyed by a **dedup key `(rule, scope)`**, with `state` (`firing`/
    `resolved`), `fired_at` / `resolved_at`, the consecutive-tick counters (for-duration + auto-resolve),
    last-notified time (re-notify window), and bounded context. **History is queryable directly from this
    table** and it **survives restart** (ALRT-07).
  - **`AlertNotification`** — a **per-channel delivery attempt** with `status` (`sent`/`failed`/`retrying`),
    attempt count, timestamp.
  - **`NotificationChannel`** — admin-configured channel (email/SMTP or generic webhook) config; secrets
    write-only.
- This is what makes dedup, hysteresis, auto-resolve, restart-recovery, and per-notification delivery status
  first-class rather than best-effort. It **supersedes** bypass-maintenance's note that "alerts =
  `AuditEvent` + health state, no dedicated alert table" — that was *that* feature declining to own a table;
  Alerting owns the alert-instance model. Critical alerts **also** write an `AuditEvent` (they are additive,
  not the primary store).

### D-ALRT-3 — Channels, thresholds & routing: admin-global channels + ownership routing

- **Channels are admin-configured, global**: one SMTP email channel + one generic HTTP webhook (v1). **No
  per-tenant self-service channels** and **no tenant-defined rules** in v1 (deferred — see Deferred Ideas).
- **Thresholds are seeded from the §9.1 defaults** already encoded in
  `control-plane/frontend/src/theme/thresholds.ts` (map_error > 0 = critical; clean/capacity ≥ 0.9 warn /
  ≥ 1.0 crit; committed-not-honored = warn; bloom-FP ≥ 1000 = warn), mirrored **server-side** as the
  authoritative defaults and made **tunable via Settings/env + per-rule admin override**. Keep the server
  defaults and `thresholds.ts` in sync (A-ALRT-4).
- **Routing is by ownership**: a **service-scoped** alert routes to the **owning tenant's contact channel +
  admin**; a **node/system-scoped** alert routes to **admin only**. A tenant is never delivered — or shown
  in history — another tenant's or a node alert (§5.2).

### D-ALRT-4 — Anti-storm: for-duration firing + hysteresis band

- **For-duration firing**: a condition must hold for **N consecutive** evaluation ticks before an alert
  opens (absorbs transient spikes).
- **Hysteresis band**: a **separate, lower clear-threshold** distinct from the fire-threshold; the alert
  auto-resolves only after the metric stays below the clear-threshold for **M consecutive** ticks — no
  flapping in the band.
- **Dedup**: at most one open alert per `(rule, scope)` key; a persisting condition updates the instance
  rather than re-opening.
- **Re-notify window**: while firing, re-notification is suppressed within a configured window; a bounded
  reminder may re-send after it (never per-tick).
- **Auto-resolve**: crossing back below the clear-threshold for M ticks resolves the alert and emits a
  **resolution notification**.
- Exact N / M / clear-band / re-notify-window values are Design/Settings choices; the *model* is fixed here.

### Agent's Discretion

- Exact lane tick interval and the `worker_alert_*` settings knobs (default N, M, clear-band deltas,
  re-notify window, retry backoff/cap) — Design picks defaults; all are Settings-tunable.
- Webhook JSON envelope schema and email template/body (subject to the "no secrets / no raw PII" rule).
- Table column details, indexes, enum names, migration ordering (after the telemetry/billing heads).
- Whether the P2 SPA alert surface is a new panel vs. an extension of an existing telemetry view (reuses the
  M5 shell + polling either way).

---

## Specific References

- **Data sources (all executed, read-only):** `NodeHealthSnapshot` (`xdp_mode`, `map_error_count`,
  `node_clean_bps`/`node_capacity_bps`, `bloom_stats`, `active_slot`/`map_version`), `TelemetryCounter`
  (per-service clean/drop pkts/bytes + drop-reason distribution → attack onset, committed-honored),
  `AgentJob` (apply `failed`, backlog), `FeedSyncRun` (feed failure), `NodeControl` (bypass/maintenance),
  `AuditEvent` (whitelist-overlaps-feed AD-003, dangerous-action toggles).
- **Threshold source of truth:** `control-plane/frontend/src/theme/thresholds.ts` (§9.1) — mirror its values
  server-side as defaults.
- **Worker lane precedent:** `control-plane/app/worker/{billing,telemetry,node_control_reconciler}.py`
  (background asyncio lanes, `run_loop`, Settings-driven cadence, no Redis `JobType`).
- **Auth/audit reuse:** `app/services/audit.py::record_event`, `require_admin`, `load_service_for_principal`
  (404 cross-tenant), `/auth/me`.
- **TDD anchors:** §9.3 (structured logging & alerting — channels, severity, hysteresis, dedup, auto-resolve,
  per-tenant isolation, "not on hot path"), §9.1 (metric thresholds table), §10.1 (apply-failed → alert),
  §7.3 (dangerous-action audit + critical alert).
- **Sibling emit-side contracts consumed here:** bypass-maintenance A-BYP-2 / ALRT-19 (bypass/maintenance
  alert event), feed-sync AD-003 (whitelist-overlap alert), apply-status (apply `failed`).

---

## Assumptions

- **A-ALRT-1** — Reuses the executed worker **background-lane pattern**; Alerting adds **one new lane**, no
  new Redis `JobType`, no change to the `Applier` boundary or the hot-path processor.
- **A-ALRT-2** — Consumes **only already-persisted** data sources + emitted audit events; **no new hot-path
  / data-plane surface and no new counters** are added by this feature.
- **A-ALRT-3** — Reuses M1 auth/RBAC (`require_admin`, `load_service_for_principal`, fail-closed tenant
  ownership) + `AuditEvent`/`record_event`; **critical alerts write audit** via `record_event`.
- **A-ALRT-4** — Server-side thresholds **mirror `theme/thresholds.ts` §9.1** as defaults (single §9.1
  semantics; the two are kept in sync); all thresholds are Settings-tunable + per-rule admin-overridable.
- **A-ALRT-5** — Alerting is the **delivery consumer** the sibling features deferred to: bypass-maintenance
  (A-BYP-2), feed-sync whitelist-overlap (AD-003), apply-status apply-`failed`; it does not modify their
  emit sites beyond reading what they persist/emit.
- **A-ALRT-6** — The **P2 SPA alert surface** reuses the executed **M5 telemetry SPA shell + REST polling
  (≤2 s, no SSE/WebSocket)** and is gated on it.
- **A-ALRT-7** — **Email = SMTP**, **webhook = a generic HTTP POST** with a JSON envelope; **no third-party
  SDKs** (no Slack/PagerDuty/SMS native integrations — the generic webhook fans out downstream).
- **A-ALRT-8** — **Execute is gated on Telemetry & dashboards executed** (data sources + reader + lane
  pattern) — **satisfied** (VERIFIED 2026-07-14). bypass-maintenance is **soft coordination only**: its
  rule (ALRT-19) simply does not fire until M6 #1 is executed (ALRT-09 fail-safe).

---

## Deferred Ideas

- **Per-tenant self-service channels & tenant-defined rules** — tenants registering their own email/webhook
  and thresholds for their services. Deferred from v1 (D-ALRT-3 keeps channels admin-global).
- **Native SMS / Slack / PagerDuty / on-call scheduling & escalation policies** — covered downstream by the
  generic webhook in v1; native integrations + escalation are a later effort.
- **Arbitrary rule DSL / query builder** — v1 is a fixed §9.3 catalog with tunable thresholds/enable, not an
  open expression engine.
- **Auto-mitigation / auto-response on alert** — M7 / OP-02 (v1 notifies only).
- **Rich incident-management UX** (grouping, timelines, assignment) beyond the P3 maintenance-silence +
  manual-ack minimum.
