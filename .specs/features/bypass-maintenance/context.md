# Bypass & Maintenance Mode — Context (Discuss output)

**Spec:** `.specs/features/bypass-maintenance/spec.md` (BYP-01..33)
**Captured:** 2026-07-10 (discuss within Specify)
**Status:** Spec draft + context drafted; awaiting approval → Design

---

## Feature Boundary

The first M6 feature — the two node-global **operator safety controls** the Pilot OLA depends on. It adds
a manual, admin-only **global soft-bypass** (emergency "stop dropping" pass-through on the XDP hot path)
and a **per-node maintenance mode** (holds config swaps during a maintenance window), both audited,
alert-worthy, banner-surfaced, and — for bypass — separately accounted.

- **Owns:** the node-control desired-state model + `/node/bypass` / `/node/maintenance` / `/node/health`
  API; the worker **node-control reconcile lane** (asserts the bypass flag into the data-plane on a fast
  tick and gates the applier's flip for maintenance); the **hot-path bypass short-circuit** (redirect
  parsed IPv4/ARP `IN→OUT`, skip the verdict pipeline); the **node-global bypass counter**; the audit +
  alert-worthy events on every toggle; and the OLA runbook.
- **Reuses, never reimplements:** M1 `require_admin` + fail-closed RBAC + `AuditEvent`/`record_event`; the
  M4 #1 agent-worker runtime + background-lane pattern (feed/telemetry) + `Applier` boundary; the M4 #2
  `active_config` write path + `xdpgw-apply` helper + pinned maps + single atomic flip; the M2
  `redirect_out()`/`tx_devmap` header-preserving forward + loader pin group + `dpstat`; the M5 telemetry
  `svc_stat` per-service counters (bypass accounting stays *separate*) + the SPA shell.
- **Does not own:** alert **delivery** (sibling M6 *Alerting*), device-level **link bypass** (M7/OP-03),
  chargeback **exclusion** of bypass bytes (M5, A-CHG-8), SLA/OLA **reporting** (sibling M6), config
  version-rollback UI (M7/OP-05), auto-engaging bypass (no auto-mitigation in v1).

**Execute status:** M4 #1 (agent-worker) is executed — the control surface, node-control model, audit,
alert event, and the worker reconcile-lane *logic* build against it now. Real data-plane effects (the
hot-path bypass pass-through, the bypass counter, and the maintenance swap-hold in the applier) are
**hard-gated on M4 #2 (double-buffer) executed**, which owns the `active_config` write path this feature
drives. This mirrors feed-sync's DP gate: the CP slice is buildable/testable ahead against a
placeholder/fake DP writer. **This is a plan-ahead M6 artifact — the current execution front is M4.**

---

## Implementation Decisions

Four gray areas — the bypass pass-through scope, the maintenance semantics, the toggle propagation path,
and bypass accounting — were resolved with the user (AskUserQuestion) after the spec draft (2026-07-10).

### D-BYP-1: Bypass pass-through scope = parsed IPv4 + ARP only (skip the verdict pipeline)

**Question:** When global bypass is active, which traffic passes `IN→OUT` — only parseable IPv4/ARP, or
every frame verbatim (incl. IPv6/fragments/malformed)?
**Decision:** Bypass **short-circuits right after a successful parse**: redirect every valid IPv4 frame +
ARP `IN→OUT`, skipping the entire verdict pipeline (service lookup → ingress cap → whitelist/VIP →
blacklist → allow-rules/rate-limit → fairness). **IPv6, malformed IPv4, and fragments still fail-fast
drop** with their existing drop reasons. Bypass reuses the existing header-preserving `redirect_out()`
(verbatim frame, TTL/checksum preserved).
**Why:** The emergency intent is "stop the *policy* engine dropping legitimate traffic," not "become an
IPv6 router." Keeping the parse fail-fast preserves v1's explicit IPv4-only forwarding commitment and all
drop-reason visibility for unsupported traffic, while removing exactly the false-drop surface (service
miss / not-allowed / rate-limit / blacklist / fairness) an operator engages bypass to escape. It also
keeps the DP change small — one branch after parse, before `service_lookup_redirect()`.
**Trade-off:** bypass is not a literal wire (IPv6/fragment/malformed still drop). This is a documented OLA
property; device-level **link bypass** (M7/OP-03) is the true fail-to-wire and is a different scope
(§11.5.1: packet-level always fail-closed; device-level chooses fail-open).

### D-BYP-2: Maintenance semantics = queue-and-apply-on-exit (hold the flip, never lose changes)

**Question:** What does maintenance mode do to config changes made while it is active — queue them and
apply on exit, reject them at the API, or hold even the built slot until an explicit apply?
**Decision:** The worker **keeps consuming jobs and building the inactive slot** but **holds the
`active_config` flip** while maintenance is on ("blocks stray `ACTIVE_SLOT_SWAP`", TDD ROADMAP M6). Config
mutations are still **accepted and queued** (202) — never rejected. On maintenance **clear**, the worker
applies the **latest built good config** with a single flip (queued changes go live together).
**Why:** This is the literal reading of "blocks stray `ACTIVE_SLOT_SWAP`" and standard maintenance-window
semantics — an operator can stage changes during the window and have them take effect cleanly on exit,
with nothing lost and no mid-window surprise swap. Rejecting mutations at the API would block staging and
push a second freeze concept up the stack; holding even the built slot until a manual apply adds an
operator step with no Pilot benefit.
**Trade-off:** during maintenance the live data-plane can lag the committed config (by design); the
worker must hold at the flip point (in the M4 #2 `DoubleBufferApplier`) rather than skip the build, so the
inactive slot stays warm for an instant exit swap. Maintenance gates only the **flip**, not CP CRUD and
not the emergency bypass control channel (D-BYP-3 / BYP-24).

### D-BYP-3: Toggle propagation = immediate control channel (fast reconcile lane, jumps the backlog)

**Question:** How does an emergency bypass toggle reach the data-plane — an immediate control channel that
jumps the service-apply backlog, or a durable `NODE_CONTROL` job in the normal ≤5 s queue?
**Decision:** A toggle writes **desired node-control state to the DB** (audited) and the worker's **fast
node-control reconcile lane** asserts it into the data-plane **ahead of the normal service-apply backlog**
— effective within ~1 tick even under a queued-apply backlog. Desired state **persists across
worker/node restart** and the worker **re-asserts** it on startup (an emergency bypass never silently
clears). No `NODE_CONTROL` `JobType`.
**Why:** Bypass is an *emergency* control — it must not wait behind a backlog of queued `SERVICE_UPDATE`
applies, which the durable-ledger path cannot guarantee. A DB desired-state row + a reconcile lane (the
feed/telemetry background-lane pattern) is naturally restart-surviving and convergent (the DP is driven to
match desired state every tick), gives a clean desired-vs-effective surface for `/node/health` (BYP-26),
and keeps the audit trail (the DB write + `AuditEvent`) without coupling emergency latency to the job
queue. The reconcile *logic* builds on the executed M4 #1 worker; only the actual DP write is M4 #2-gated.
**Trade-off:** node-control state lives outside the `AgentJob` ledger (a second, small state surface) and
the reconcile lane is a new worker background task; convergence latency is bounded by the fast tick
interval (sub-second-to-second class, tunable) rather than a single synchronous write.

### D-BYP-4: Bypass accounting = add a node-global exact per-CPU bypass counter now

**Question:** Add a data-plane counter for bypass-forwarded traffic now (the "counted separately"
requirement), or defer it to chargeback?
**Decision:** **Add now** — an exact per-CPU **node-global** bypass packets/bytes counter, **separate**
from telemetry's per-service `svc_stat` clean counters, surfaced via `dpstat` and `/node/health`. While
bypass is active, bypassed frames increment this counter and **do not** increment per-service clean
counters.
**Why:** TDD §10.3 requires bypass traffic "counted separately (not clean-scrubbed) for chargeback
reconciliation," and chargeback's A-CHG-8 forward-depends on exactly this. The counter is cheap (a single
node-global per-CPU array, no per-service attribution needed while bypassed) and closes the accounting
loop at the point the bypass behavior is built, rather than leaving a dangling forward dependency.
**Trade-off:** no per-service attribution of bypassed traffic (node-global only) — acceptable because
bypass is an all-services emergency and chargeback only needs to *exclude* bypass bytes, not attribute
them. The actual *exclusion* from `BillingUsage` remains M5 chargeback's job (A-CHG-8); this feature only
produces the counter.

---

## Assumptions (flagged for Design)

- **A-BYP-1 — Node-global, admin-only.** Both controls and all `/node/*` endpoints are RBAC-admin
  (`require_admin`, fail-closed); neither is tenant-scoped or tenant-managed.
- **A-BYP-2 — "Alert" in v1 = audit + emitted event.** Every toggle writes a dangerous-action
  `AuditEvent` and emits a **critical alert-worthy** record/event; email/webhook **delivery** is the
  sibling M6 *Alerting* feature (consumes it). Mirrors feed-sync A-FEED-4.
- **A-BYP-3 — `active_config` bypass indicator + non-racing flip (shared contract).** The hot path gains a
  bypass indicator read at ingress. Storage — a new field in `struct active_config` (field-preserving
  read-modify-write on both the control-channel write and the M4 #2 slot flip) **vs** a dedicated tiny
  `node_control` map the hot path also reads (fully independent of the flip) — is a **Design call**. Either
  way the M4 #2 flip MUST preserve the bypass indicator and toggling bypass MUST NOT alter
  `active_slot`/`version`. Flag to the M4 #2 applier as a shared contract (like telemetry's `dp_id`).
- **A-BYP-4 — New `NodeControl` desired-state model.** Desired bypass/maintenance state persists in a new
  additive singleton-ish `NodeControl` row (or `node_control` table): `bypass_enabled`,
  `maintenance_enabled`, `bypass_reason`, `bypass_activated_at`, `maintenance_activated_at`, actor. New
  model + migration only (no M1–M5 schema change). Exact shape (singleton row vs keyed by node id for a
  future multi-node world) = Design call; Pilot is single-node.
- **A-BYP-5 — Worker node-control reconcile lane.** A new worker **background asyncio lane** (feed/
  telemetry lane pattern) reconciles `NodeControl` → data-plane every fast tick: assert/clear the bypass
  indicator, and gate the `DoubleBufferApplier` flip when maintenance is on. Buildable on the executed
  M4 #1 worker; the real DP write is M4 #2-gated. Tick interval is a new `worker_node_control_*` setting
  (fast, sub-service-apply class).
- **A-BYP-6 — Bypass counter lives outside `counter_map`.** The node-global bypass counter is a new exact
  per-CPU counter **outside** the frozen `counter_map` drop-reason ABI (bypass is not a drop) — mirrors
  `bloom_hit_lpm_miss` (AD-023). Surfaced via a new `dpstat` field + `/node/health`.
- **A-BYP-7 — Bypass forward = the existing header-preserving redirect.** Bypass reuses `redirect_out()`/
  `tx_devmap` verbatim (TTL/checksum preserved, no L3 mutation) — the same forward mechanics as normal
  clean traffic; it does not add a new redirect path. This soft in-XDP bypass is distinct from device-
  level **link bypass** (M7/OP-03) on process/host death (§11.5.1 fail-policy distinction).
- **A-BYP-8 — Independence & interaction.** Bypass and maintenance are independent and may both be active:
  maintenance gates only the config **flip**; the emergency **bypass** control channel is not gated by
  maintenance, and the hot-path bypass takes effect regardless of maintenance. Clearing one never clears
  the other. `/node/health` reports both `desired` and `effective` for each (BYP-26) so a toggle stuck
  behind a down worker / unloaded DP is visible.

---

## Open Questions for Design

1. **Bypass indicator storage** — extend `struct active_config` with a `bypass`/`flags` field (RMW-safe
   against the flip) vs a dedicated `node_control` BPF map the hot path also reads (A-BYP-3). The map
   avoids all flip-vs-toggle race reasoning but adds a per-packet lookup; the field is one extra read but
   needs a field-preserving flip contract with the M4 #2 applier.
2. **Maintenance swap-hold mechanism** — where the `DoubleBufferApplier` checks maintenance (build-then-
   hold-at-flip) and how the queued-apply-on-exit is triggered (reconcile-lane re-drives the last built
   config vs the applier re-reads desired state at flip time).
3. **Node-control channel** — DB desired-state + reconcile-lane assert (chosen, D-BYP-3): the exact
   worker→DP assert path (does the reconcile lane call the `xdpgw-apply` helper with a "set bypass" mode,
   or a smaller dedicated control write?), the fast-tick interval default, and how it stays ahead of the
   service-apply lane.
4. **Alert-event shape** — reuse `AuditEvent` alone vs a dedicated alert record/event the sibling Alerting
   feature consumes; severity/dedup semantics (align with the Alerting feature's model when it lands).
5. **`NodeControl` model shape** — singleton row vs node-keyed; where `activated_at`/duration and
   desired-vs-effective are computed (DB vs `/node/health` handler reading live DP state via the reader).
6. **Reader for `/node/health`** — reuse the telemetry `TelemetryReader`/`dpstat snapshot --json` C-helper
   pattern for XDP mode / map version / bypass counter, vs a dedicated small reader (M5 telemetry already
   introduces the node-health reader — coordinate to avoid duplication).
