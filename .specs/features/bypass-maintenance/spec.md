# Bypass & Maintenance Mode Specification

**Feature:** M6 #1 — Bypass & maintenance mode
**Context:** `.specs/features/bypass-maintenance/context.md` (D-BYP-1..4, A-BYP-1..8)
**Status:** **Draft** (2026-07-10, awaiting approval) → Design
**Depends on (Execute-gated):**
- **agent-worker executed** (M4 #1, **satisfied**) — provides the long-running worker, its periodic
  reconcile tick and the background-lane pattern (feed/telemetry) this feature's node-control reconcile
  lane reuses, and the `Applier` boundary maintenance mode gates.
- **double-buffer executed** (M4 #2) — provides the `active_config` write path, the `xdpgw-apply` helper +
  pinned maps, and the single atomic slot flip. The P1 *Data-plane soft-bypass pass-through & accounting*
  and the maintenance **swap-hold** are **hard-gated** on it; the control surface / audit / state / worker
  lane build against the executed worker ahead of it (agent-worker placeholder precedent).
- **Reuses (executed):** M1 auth/RBAC (`require_admin`, fail-closed ownership) + `AuditEvent` +
  `record_event`; M2 loader pin group, `dpstat`, and the `redirect_out()`/`tx_devmap` header-preserving
  `IN→OUT` forward; M5 telemetry `svc_stat` per-service counters (bypass accounting stays *separate* from
  them) + the SPA shell (banner, P2-gated).

---

## Problem Statement

The gateway is a single-node, fail-closed inline device (a deliberate SPOF at Pilot). When an operator
suspects the scrubber is dropping legitimate traffic node-wide, or needs to perform a maintenance window
without a stray config swap taking effect mid-work, there is today **no operator control** to intervene —
the only levers are device-level link bypass (M7) and redeploy. This feature adds the two node-global
operator safety controls the OLA depends on: a **global soft-bypass** flag that makes the XDP hot path
pass clean traffic straight through (skipping the verdict pipeline) as an emergency "stop dropping"
switch, and a **per-node maintenance mode** that holds config swaps during a maintenance window. Both are
admin-only, audited, alert-worthy, surfaced as a banner, and — for bypass — accounted separately so
bypassed traffic is never billed as scrubbed clean.

## Goals

- [ ] An admin can **engage/clear a global soft-bypass** that takes effect on the XDP hot path near-
      instantly (ahead of any service-apply backlog), survives a worker/node restart, and returns the full
      verdict pipeline on clear — every toggle audited and alert-worthy.
- [ ] While bypass is active the hot path **passes parsed IPv4 + ARP through `IN→OUT`** (header-preserving)
      and drops nothing on policy grounds; v1's IPv4-only forwarding commitment is unchanged (IPv6 /
      malformed / fragments still fail-fast drop).
- [ ] Bypass-forwarded traffic is **counted separately** (node-global exact per-CPU counter, not per-
      service clean-scrubbed) so chargeback can exclude it (TDD §10.3 / A-CHG-8).
- [ ] An admin can **engage/clear per-node maintenance mode** that holds the `active_config` flip (blocks
      stray `ACTIVE_SLOT_SWAP`) while still accepting and queueing config changes, then applies the latest
      good config on exit — every toggle audited.
- [ ] Bypass and maintenance **state is surfaced** (`GET /node/health` + a dashboard banner) and every
      activation **emits a critical alert-worthy event** the M6 Alerting feature delivers.

## Out of Scope

| Feature | Reason |
| --- | --- |
| Alert **delivery** (email / webhook) | Sibling M6 *Alerting* feature consumes the audit/alert events this feature emits (A-BYP-2) |
| Device-level **link bypass** (NIC fail-to-wire) on process/host death | M7 / OP-03 — device-level fail-open is a different scope from this soft in-XDP flag (D-BYP-1 / A-BYP-7); §11.5.1 |
| Chargeback **exclusion** of bypass bytes from billing | This feature only *produces* the separate counter; excluding it from `BillingUsage` is M5 chargeback (A-CHG-8) |
| Per-tenant / tenant-managed bypass or maintenance | Both controls are node-global, admin-only (A-BYP-1) |
| SLA / OLA **reporting** | Sibling M6 *SLA/OLA reporting & audit* feature |
| One-click config **rollback** UI (OP-05) | M7 — this feature holds swaps (maintenance) and bypasses filtering, it does not add a version-rollback control |
| Auto-engaging bypass on detected mass false-drop | v1 is manual operator control only (no auto-mitigation, PROJECT scope) |

---

## User Stories

### P1: Global bypass operator control ⭐ MVP

**User Story:** As a **system admin**, I want to engage and clear a global soft-bypass near-instantly so I
can stop the gateway dropping legitimate traffic during an incident, with a full audit trail.

**Why P1:** This is the emergency lever the OLA/rollback plan (TDD §10.1) depends on; nothing else in this
feature matters without the operator control surface.

**Acceptance Criteria:**

1. WHEN an admin `POST /node/bypass {enabled: true}` (optionally with a `reason`) THEN the system SHALL
   record the desired node bypass state and return the current desired + effective bypass state. `(BYP-01)`
2. WHEN bypass is toggled on or off THEN the system SHALL write a **dangerous-action** audit event (actor,
   on/off, reason, ip) — §7.3. `(BYP-02)`
3. WHEN a non-admin calls `/node/bypass` THEN the system SHALL fail closed (403, no state change, no
   leak). `(BYP-03)`
4. WHEN bypass is set on THEN it SHALL propagate to the data-plane via the **immediate control channel**
   (a fast worker reconcile tick) **ahead of the normal service-apply backlog** — effective within one
   tick even under a queued-apply backlog, never waiting behind queued `SERVICE_UPDATE` jobs. `(BYP-04)`
5. WHEN the worker or node restarts while bypass is on THEN the worker SHALL **re-assert** the flag from
   persisted desired state on startup (an emergency bypass never silently clears). `(BYP-05)`
6. WHEN bypass is cleared THEN the system SHALL restore the full verdict pipeline (the current
   `active_slot`) and write an audit event. `(BYP-06)`
7. WHEN bypass is set on when already on (or off when already off) THEN the toggle SHALL be idempotent — no
   duplicate side effects, no spurious second alert. `(BYP-07)`
8. WHEN bypass is engaged THEN the system SHALL record `activated_at` and the operator `reason` on the
   node state for the OLA runbook trail. `(BYP-08)`

**Independent Test:** `POST /node/bypass {enabled:true}` as admin, assert 200 + audit event + persisted
desired state; as non-admin assert 403; toggle off and assert audit + state cleared; assert a second
identical toggle is a no-op.

---

### P1: Data-plane soft-bypass pass-through & separate accounting ⭐ MVP *(gated on M4 #2 executed)*

**User Story:** As a **system admin**, I want the XDP hot path to pass clean traffic straight through when
bypass is engaged so the gateway stops enforcing policy — while bypassed traffic is not billed as scrubbed.

**Why P1:** A bypass flag that the hot path ignores is inert; this is the actual emergency behavior and
the separate accounting the chargeback contract requires.

**Acceptance Criteria:**

1. WHEN the `active_config` bypass indicator is set THEN, after a successful parse, the hot path SHALL
   redirect every valid IPv4 frame `IN→OUT` **skipping the entire verdict pipeline** (service lookup,
   ingress cap, whitelist/VIP, blacklist, allow-rules, rate-limit, fairness). `(BYP-09)`
2. WHEN bypass is active THEN ARP SHALL redirect `IN→OUT` (unchanged from normal). `(BYP-10)`
3. WHEN bypass is active THEN IPv6, malformed IPv4, and fragments SHALL **still** fail-fast drop with their
   existing drop reasons — v1's IPv4-only forwarding commitment is unchanged (D-BYP-1). `(BYP-11)`
4. WHEN bypass forwards a frame THEN it SHALL use the existing header-preserving redirect (verbatim frame,
   TTL/checksum preserved, no L3 mutation) — identical forward mechanics to normal clean traffic
   (A-BYP-7). `(BYP-12)`
5. WHEN the hot path reads the bypass indicator THEN it SHALL read it **consistently per-packet** at
   ingress, and toggling it SHALL never tear the `active_slot` view (no partial/torn read). `(BYP-13)`
6. WHEN bypass is cleared THEN the hot path SHALL return to the full verdict pipeline with **no residual
   state** (the next packet is enforced normally). `(BYP-14)`
7. WHEN bypass forwards a frame THEN the system SHALL count it in an **exact per-CPU node-global bypass**
   packet/byte counter — separate from the per-service `svc_stat` clean counters (bypassed bytes are NOT
   counted as clean-scrubbed). `(BYP-15)`
8. WHEN bypass is active THEN per-service clean counters SHALL NOT increment for bypassed traffic, so
   chargeback can exclude bypass clean from `BillingUsage` (A-CHG-8, M5). `(BYP-16)`
9. WHEN an operator reads the data-plane THEN the bypass counter SHALL be exposed via `dpstat` and
   `GET /node/health`. `(BYP-17)`

**Independent Test:** With M4 #2 loaded, set bypass on; via the DP verdict harness / `dpstat` assert a
frame that would normally `service_miss`/`not_allowed`/`blacklist_drop` is instead redirected `IN→OUT`,
the bypass counter advances, `svc_stat` clean does not; assert IPv6/fragment still drop; clear and assert
enforcement resumes.

---

### P1: Per-node maintenance mode ⭐ MVP *(swap-hold gated on M4 #2 executed)*

**User Story:** As a **system admin**, I want to put the node in maintenance so a stray config swap can't
take effect mid-window, while my staged changes still queue and apply cleanly when I exit maintenance.

**Why P1:** The maintenance window is half of the OLA operational-safety commitment (ROADMAP M6 target).

**Acceptance Criteria:**

1. WHEN an admin `POST /node/maintenance {enabled: true}` THEN the system SHALL record the desired
   maintenance state, write a dangerous-action audit event, and return the state. `(BYP-18)`
2. WHEN a non-admin calls `/node/maintenance` THEN the system SHALL fail closed (403). `(BYP-19)`
3. WHEN maintenance is on THEN the worker SHALL continue consuming jobs and building the inactive slot but
   SHALL **hold the `active_config` flip** — no `ACTIVE_SLOT_SWAP` takes effect (D-BYP-2). `(BYP-20)`
4. WHEN a config mutation is made during maintenance THEN it SHALL still be **accepted and queued** (202),
   never rejected or lost. `(BYP-21)`
5. WHEN maintenance is cleared THEN the worker SHALL apply the **latest built good config** with a single
   flip (queued changes go live on exit). `(BYP-22)`
6. WHEN the worker restarts during a maintenance window THEN the maintenance state SHALL persist and the
   swap-hold SHALL still be in effect on startup (A-BYP-4). `(BYP-23)`
7. WHEN maintenance is engaged THEN emergency **bypass SHALL remain independently operable** — the bypass
   control channel is NOT gated by maintenance (an operator can bypass during a maintenance window). `(BYP-24)`

**Independent Test:** Engage maintenance; make a service mutation and assert it queues (202) but the active
version does not advance; clear maintenance and assert the queued config swaps in with one flip; engage
maintenance then bypass and assert bypass still takes effect.

---

### P1: Node state surface & alert event ⭐ MVP

**User Story:** As a **system admin**, I want to see the node's bypass/maintenance state (and be alerted on
change) so operators and the dashboard always know when protection is bypassed or frozen.

**Why P1:** State that no one can observe is unsafe; the banner and the critical alert are mandated (TDD
§7.3/§9 — "BYPASS ACTIVE" banner + critical alert).

**Acceptance Criteria:**

1. WHEN an admin `GET /node/health` THEN the system SHALL expose **effective** bypass state, maintenance
   state, XDP mode, active map version/slot, and the bypass counter. `(BYP-25)`
2. WHEN bypass or maintenance is requested but not yet asserted (M4 #2 write path offline / worker down)
   THEN `/node/health` SHALL distinguish **desired vs effective** state (so a stuck toggle is visible).
   `(BYP-26)`
3. WHEN bypass or maintenance is toggled THEN the system SHALL emit a **critical alert-worthy** event /
   record consumable by the M6 Alerting feature (delivery deferred, A-BYP-2). `(BYP-27)`

**Independent Test:** Toggle bypass and maintenance; assert `/node/health` reflects effective + desired
state and the bypass counter; assert an alert-worthy event/record is emitted per toggle.

---

### P2: Dashboard bypass/maintenance banner *(gated on telemetry SPA shell executed)*

**User Story:** As a **dashboard user**, I want an unmissable banner whenever the node is bypassed or in
maintenance so no one mistakes a bypassed gateway for a protecting one.

**Why P2:** The visual banner layers on the P1 `/node/health` state; the state (and safety) exists without
it, and it depends on the M5 telemetry SPA shell.

**Acceptance Criteria:**

1. WHEN effective bypass is on THEN the SPA shell SHALL render a persistent critical-styled **"BYPASS
   ACTIVE"** banner on every view. `(BYP-28)`
2. WHEN maintenance is on THEN the SPA shell SHALL render a **"MAINTENANCE"** indicator on every view.
   `(BYP-29)`
3. WHEN state changes THEN the banner SHALL reflect it within the telemetry poll cadence (≤2 s). `(BYP-30)`

**Independent Test:** With the SPA running, toggle bypass/maintenance via API and assert the banner appears
/ clears within one poll interval on any route.

---

### P3: OLA runbook & toggle history

**User Story:** As a **system admin / SRE**, I want a documented runbook and a toggle history so post-
incident reviews can reconstruct when and why protection was bypassed.

**Why P3:** Operational ergonomics and documentation — not required to make the controls work, but required
for the OLA to be complete.

**Acceptance Criteria:**

1. WHEN reviewing an incident THEN the bypass/maintenance toggle history SHALL be queryable (audit-event
   backed: who, when, on/off, reason). `(BYP-31)`
2. WHEN operating the node THEN an **OLA runbook** SHALL document when/how to engage bypass and maintenance,
   the chargeback/accounting implications, and the exit procedure. `(BYP-32)`
3. WHEN bypass or maintenance is active THEN `/node/health` SHALL surface **how long** it has been active
   (`activated_at` / duration) for runbook time-tracking. `(BYP-33)`

**Independent Test:** Toggle bypass twice with reasons, query the history and assert both toggles with
actor/reason/timestamp; assert `/node/health` reports active duration.

---

## Edge Cases

- WHEN bypass is requested but the M4 #2 write path is unavailable (worker down / DP not loaded) THEN the
  desired state SHALL persist and `/node/health` SHALL show `desired=on, effective=off` until the worker
  re-asserts it — the request is never silently dropped. `(→ BYP-05, BYP-26)`
- WHEN both bypass and maintenance are engaged THEN both SHALL hold independently — bypass forwards on the
  hot path while maintenance holds swaps; clearing one SHALL NOT clear the other. `(→ BYP-24)`
- WHEN a service-apply backlog exists and bypass is toggled THEN bypass SHALL take effect ahead of the
  backlog (emergency latency), not behind it. `(→ BYP-04)`
- WHEN maintenance is cleared but no config changed during the window THEN the exit SHALL be a no-op (no
  spurious swap). `(→ BYP-22)`
- WHEN the bypass flag storage and the slot flip race THEN neither SHALL clobber the other — a flip during
  bypass SHALL preserve the bypass indicator, and toggling bypass SHALL NOT alter `active_slot`/`version`
  (A-BYP-3). `(→ BYP-13)`
- WHEN a toggle carries a `reason` longer than the stored bound THEN it SHALL be rejected (422) rather than
  silently truncated. `(→ BYP-01)`
- WHEN bypass forwards traffic THEN drop-reason counters SHALL NOT advance for policy reasons (nothing is
  dropped on policy grounds while bypassed) — only parse-level drops (IPv6/malformed/fragment) still count.
  `(→ BYP-11, BYP-15)`

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| BYP-01 | P1: Global bypass control | Design | Pending |
| BYP-02 | P1: Global bypass control | Design | Pending |
| BYP-03 | P1: Global bypass control | Design | Pending |
| BYP-04 | P1: Global bypass control | Design | Pending |
| BYP-05 | P1: Global bypass control | Design | Pending |
| BYP-06 | P1: Global bypass control | Design | Pending |
| BYP-07 | P1: Global bypass control | Design | Pending |
| BYP-08 | P1: Global bypass control | Design | Pending |
| BYP-09 | P1: DP pass-through & accounting | Design | Pending |
| BYP-10 | P1: DP pass-through & accounting | Design | Pending |
| BYP-11 | P1: DP pass-through & accounting | Design | Pending |
| BYP-12 | P1: DP pass-through & accounting | Design | Pending |
| BYP-13 | P1: DP pass-through & accounting | Design | Pending |
| BYP-14 | P1: DP pass-through & accounting | Design | Pending |
| BYP-15 | P1: DP pass-through & accounting | Design | Pending |
| BYP-16 | P1: DP pass-through & accounting | Design | Pending |
| BYP-17 | P1: DP pass-through & accounting | Design | Pending |
| BYP-18 | P1: Maintenance mode | Design | Pending |
| BYP-19 | P1: Maintenance mode | Design | Pending |
| BYP-20 | P1: Maintenance mode | Design | Pending |
| BYP-21 | P1: Maintenance mode | Design | Pending |
| BYP-22 | P1: Maintenance mode | Design | Pending |
| BYP-23 | P1: Maintenance mode | Design | Pending |
| BYP-24 | P1: Maintenance mode | Design | Pending |
| BYP-25 | P1: Node state & alert event | Design | Pending |
| BYP-26 | P1: Node state & alert event | Design | Pending |
| BYP-27 | P1: Node state & alert event | Design | Pending |
| BYP-28 | P2: Dashboard banner | Design | Pending |
| BYP-29 | P2: Dashboard banner | Design | Pending |
| BYP-30 | P2: Dashboard banner | Design | Pending |
| BYP-31 | P3: OLA runbook & history | Design | Pending |
| BYP-32 | P3: OLA runbook & history | Design | Pending |
| BYP-33 | P3: OLA runbook & history | Design | Pending |

**ID format:** `BYP-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 33 total, 0 mapped to Design/Tasks yet (spec draft). P1 = BYP-01..27, P2 = BYP-28..30,
P3 = BYP-31..33.

---

## Success Criteria

- [ ] An admin engages global bypass and, with M4 #2 loaded, traffic that would normally be dropped on
      policy grounds is instead forwarded `IN→OUT` within one worker tick — even under a service-apply
      backlog — and the full pipeline returns on clear; every toggle audited + alert-worthy.
- [ ] Bypass survives a worker/node restart (re-asserted from persisted desired state) and never silently
      clears.
- [ ] Bypassed traffic is counted in a node-global bypass counter distinct from per-service clean bytes
      (verifiable via `dpstat` / `/node/health`), so chargeback can exclude it.
- [ ] Engaging maintenance holds the `active_config` flip while config mutations still queue (202); exiting
      maintenance applies the latest good config with a single swap; the window survives a worker restart.
- [ ] `/node/health` reports effective + desired bypass/maintenance state, active duration, and the bypass
      counter; the SPA shows an unmissable "BYPASS ACTIVE" / "MAINTENANCE" banner (with the M5 SPA shell).
