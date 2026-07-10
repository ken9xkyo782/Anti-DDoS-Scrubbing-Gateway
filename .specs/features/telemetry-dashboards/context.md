# Telemetry & Dashboards — Context (Gray-Area Decisions)

Decisions captured during Specify for gray areas with multiple valid approaches. Referenced by `spec.md` and carried into Design.

## Decisions

### D-TEL-1: Telemetry introduces per-service hot-path counters

**Question:** The XDP hot path has only node-global counters (`counter_map`, 16 §9.2 reasons) plus per-service token-bucket *state* — no per-service packet/byte counters. How far into the data-plane does this feature reach?

**Decision:** This feature **adds exact per-CPU per-service counters** to the hot path: clean packets/bytes, drop packets/bytes, and per-service per-drop-reason counts. Node-global `counter_map` stays authoritative for pre-service/unmatched drops.

**Reason:** Tenant per-service telemetry (§9.1) is impossible without it. Introducing it here (rather than in the sibling *Chargeback metering*) makes telemetry a true end-to-end vertical slice, and the **exact clean-byte counter** becomes the billing source of truth that chargeback reuses — satisfying the PROJECT constraint that hot-path billing bytes be exact per-CPU counters, decoupled from sampled events.

**Impact:** New data-plane per-service counter map (unslotted runtime state, bounded to the 1,000-service envelope, reset-on-reload); hot-path increment at the clean-redirect point and every service-scoped drop; DP test-suite baseline extended. Chargeback metering (M5 sibling) reuses `clean_bytes` for p95.

### D-TEL-2: Frontend scope = SPA shell + telemetry/health views only

**Question:** No React SPA exists. What frontend belongs to this feature?

**Decision:** Bootstrap the React SPA (session login, role-aware routing tenant/admin, base layout) and build **only** the telemetry + node-health views. Config CRUD screens (services/rules/lists/feeds) are a **separate later effort**.

**Reason:** The dashboard is the user-facing payoff and half the feature name, but bundling all M1–M4 CRUD UIs would balloon scope far beyond observability. The SPA shell is shared infrastructure the later CRUD effort inherits.

**Impact:** First frontend in the repo (new `control-plane/frontend` or equivalent). Establishes auth/session, routing, layout, and the API-polling pattern reused by all future UI. CRUD-screen work is out of scope (tracked as a follow-on).

### D-TEL-3: Realtime via REST polling (not server push)

**Question:** How to hit the ≤2 s refresh target?

**Decision:** Worker aggregates windowed `TelemetryCounter` rows; the SPA **polls** the telemetry API on a ≤2 s interval. No SSE/WebSocket.

**Reason:** Simplest transport that meets ≤2 s, stateless, and fits FastAPI + React cleanly. Server push adds streaming transport, connection state, and per-tenant fan-out complexity unjustified at pilot. Revisit only if polling cannot meet the target.

**Impact:** Aggregation cadence ≤2 s; API serves latest window(s); SPA polls. `window_ts`/`window_seconds` in responses let the client render staleness.

### D-TEL-4: Include sampled top-talkers (top dst-port + top src IP)

**Question:** Show top-talkers? `top_src` is source-IP PII.

**Decision:** Aggregate **both** top dst-ports and top src IPs from the sampled `drop_ringbuf` (approximate). Accept the **pilot PII posture** for `top_src` (CM-08 defers retention/anonymization to GA).

**Reason:** Richest attack-shape visibility; the sampling infrastructure (ringbuf + `sample_stats`) already exists. PII exposure is a known, product-accepted pilot risk with a GA remediation path.

**Impact:** Aggregator maintains rolling top-N ports/IPs per service+node; UI labels them sampled/approximate. `top_src` handling noted under CM-08. Scoped to P2 (additive to the P1 counter core).

## Assumptions (validate in Design)

- **A-TEL-1:** The worker runs on the gateway node and can read the pinned data-plane maps (`/sys/fs/bpf/xdp_gateway/`) via the established C-helper subprocess pattern (dpstat/`xdpgw-apply` precedent) emitting structured output — no new privileged surface beyond what the worker already has.
- **A-TEL-2:** `TELEMETRY_AGGREGATE` is a **new `JobType` + handler** plugged into the executed worker `HANDLERS` registry (STATE line 52), driven by an in-worker periodic scheduler (the feed-sync due-time scheduler is the pattern). It is not enqueued by config mutations.
- **A-TEL-3:** `TelemetryCounter` is a new Postgres model keyed by `(service_id | node, window_ts)` with `window_seconds`, clean/drop pkts+bytes, per-reason drop counts (columns or JSONB), derived pps/bps, and sampled top-N (JSONB). Bounded by a retention prune. Migration is additive (no change to M1–M4 schema).
- **A-TEL-4:** `/services/{id}/telemetry` and the node health/telemetry endpoint reuse the existing session/RBAC middleware and tenant-ownership guard verbatim — no new auth mechanism.
- **A-TEL-5:** Node health fields sourced as: XDP mode + active map version from the loader/`active_config` (via the C reader); apply status + worker backlog from control-plane DB (`AgentJob`/service `apply_status`); feed status from `FeedSyncRun` (M4#3); `map_error` from `counter_map` index 15; throughput-vs-capacity from aggregated clean-bps vs `node_clean_capacity` (fairness seed/env).
- **A-TEL-6:** Per-service counters are **unslotted** (like `counter_map`) and therefore untouched by the double-buffer config swap; the aggregator reconciles stale keys against the set of active services.
- **A-TEL-7:** Threat-feed-sync and fairness features are executed/available by the time this feature's P2 admin panels (feed status, committed-honored) are built; P1 does not depend on them.
- **A-TEL-8:** Added-latency p99 (§9.1) has no in-band measurement path in v1 and is excluded from this feature (see Out of Scope).
