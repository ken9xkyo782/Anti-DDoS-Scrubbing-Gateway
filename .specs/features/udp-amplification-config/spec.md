# UDP Amplification Config & DDoS Protection Tab Specification

**Milestone:** M5/M6 cross-cutting — *Frontend / operability* track (admin operability; realizes a
deferred data-plane item from M3 *Blacklist & deny filters*).
**Category ID:** AMP
**SPA tab label:** **DDoS Protection** (first content = UDP amplification; deliberately named broader
than its v1 scope so future deny-filter controls — bogon policy, per-port rate-limits — can join it).
**Status:** Spec + context drafted (2026-07-21) — 3 gray areas resolved via AskUserQuestion
(D-AMP-1..3, `context.md`). Awaiting approval → Design.

**What this feature is:** the **control-plane writer** for the dynamic `udp_blocked_port_bitmap` that
*Blacklist & deny filters* (BLK) shipped with **enforcement only** — its v1 writer was the loader env
seed, and a control-plane surface was **explicitly deferred** (D-BLK-2 / AD-022, recorded as a
STATE.md deferred idea: "Control-plane writer for `udp_blocked_port_bitmap` (minimal admin CRUD +
build path)"). This feature builds that surface end-to-end: an admin model + API, a worker reconcile
lane that pushes the desired port set into the pinned BPF map via a new `dpstat` subcommand, and a new
admin SPA tab.

**Depends on (all executed / present in-tree — this feature adds no new hot-path code):**

- **Blacklist & deny filters** (`.specs/features/blacklist-filters/`, **VERIFIED**) — owns the
  enforcement that this feature configures: the always-on hardcoded amplification set
  (`amp_port_hardcoded`, [`blacklist.h`](../../../data-plane/src/blacklist.h)) and the **slotted**
  `udp_blocked_port_bitmap` (`ARRAY_OF_MAPS[2]` of inner `ARRAY[1024]×u64`, one bit per UDP source
  port 0..65535; `word = port>>6`, `bit = 1<<(port&63)`). Both drop `udp_amplification_drop`
  (frozen ABI index 7). **This feature is purely a writer/UI over the existing map — no drop-reason
  change, no new stage, no hot-path edit.**
- **Double-buffer map build/swap** (`.specs/features/double-buffer-swap/`, M4 #2) — `xdpgw-apply`
  **carries the bitmap forward** (active→inactive `apply_copy_outer_inner`) on every service and
  global-deny apply, so admin-configured ports must survive an unrelated config swap (AMP-11).
- **Agent worker & job pipeline** (`.specs/features/agent-worker/`, **VERIFIED**) — the worker
  runtime + the background-lane pattern the reconcile lane mirrors.
- **Bypass & maintenance mode** / **Static next-hop rewrite** — the **exact precedent, present in
  code**: `DpstatBypassWriter` + `NodeControlReconciler`
  ([`worker/node_control_reconciler.py`](../../../control-plane/app/worker/node_control_reconciler.py)),
  `NextHopResolver` + `DpstatNextHopWriter`
  ([`worker/nexthop_resolver.py`](../../../control-plane/app/worker/nexthop_resolver.py)), and
  `dpstat`'s `set-bypass` / `set-nexthop` privileged writer subcommands. AMP's lane + `dpstat
  set-blocked-ports` is a 1:1 clone of this pattern.
- **Configuration management SPA** (`.specs/features/config-management-spa/`, AD-034) — the admin
  shell, role-filtered `Sidebar`, `ui/` primitives, `apiClient` (`{detail}` parse +
  `fieldErrorsFrom422`), and TanStack Query patterns the new tab reuses.
- **Auth & RBAC** — `require_admin`, `AuditEvent` / `record_event`, `scrub_metadata` (node-global
  config is admin-only, mirroring Threat Feeds / Global Blacklist / Node Control).

**Decisions already made (bind this spec):**

- **D-BLK-2 / AD-022:** the bitmap is a slotted config map whose v1 writer was the seed helper only;
  the control-plane writer was deferred — **this feature is that writer**. The hardcoded set stays
  compile-time (a rebuild to change); this feature never touches it.
- **A-BLK-7:** the dynamic bitmap is **node-global** (one bitmap, all services) — so the model is a
  single node-global port list, admin-only. No per-service or per-tenant dimension.
- **PRD §8.2:** a UDP source port set in the dynamic bitmap drops `udp_amplification_drop` — the same
  reason and stage position the hardcoded set uses. This feature adds no new reason and no new stage.
- **D-AMP-1..3 (this spec, resolved via AskUserQuestion — see `context.md`):** v1 scope = dynamic
  port list CRUD + read-only view of the built-in set (enforcement stays always-on, **no** node-wide
  toggle, **no** per-built-in override); propagation = **worker reconcile lane + `dpstat`
  subcommand** (no apply-snapshot wire-format change); entry model = **single port + optional note**,
  node-global, admin-only, **no expiry**.

## Problem Statement

The gateway drops UDP reflection/amplification floods from a hardcoded source-port set and from a
"dynamic" `udp_blocked_port_bitmap`, but the dynamic bitmap has **no operator interface**: its only
writer is a loader environment variable (`XDPGW_SEED_BLOCKED_PORT`) applied at load time. When a novel
reflection vector appears mid-pilot (a game-server query port, WS-Discovery, a new UDP service abused
as an amplifier), an admin cannot block its source port without a rebuild/reload — the "dynamic"
promise is unfulfilled (D-BLK-2 shipped enforcement, deferred the writer). This feature closes that
gap with an admin-managed, node-global blocked-port list that reaches the data plane in seconds
through the same reconcile-lane pattern already used for bypass and next-hop, surfaced in a new
**DDoS Protection** admin dashboard tab.

## Goals

- [ ] An admin can add and remove UDP source ports to block, node-wide, from the dashboard; a blocked
      port causes matching UDP traffic to drop `udp_amplification_drop` within the reconcile SLA
      (target ≤ 5 s, consistent with PRD config-propagation), with no rebuild and no service re-apply.
- [ ] The change survives unrelated config activity: a service or feed apply (which rebuilds/carries
      the double-buffer slots) never drops admin-configured ports, and a worker restart re-asserts the
      desired set from Postgres (Postgres is the source of truth; the BPF map is derived state).
- [ ] The admin can see the always-on **built-in** amplification port set (read-only) alongside the
      dynamic list, so the tab explains what is already blocked without a config change.
- [ ] Zero hot-path change, zero new drop reason, zero apply-snapshot wire-format change — the feature
      is a new CP model + API + worker lane + `dpstat` subcommand + SPA tab over the existing,
      verified enforcement stage.
- [ ] Every mutation is admin-only (403 for tenant users) and audited (create/delete in the
      dangerous-action taxonomy, mirroring feed/global-blacklist/node-control writes).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| --- | --- |
| Node-wide enable/disable **toggle** for amplification protection | Q1 option (b), **not chosen** (D-AMP-1). Enforcement stays always-on; a kill-switch would add a new DP runtime flag + hot-path branch. Deferred idea. |
| Per-built-in-port **override** (disable individual hardcoded ports) | Q1 option (c), **not chosen** (D-AMP-1). Contradicts the PRD's "hardcoded" intent (GA-1c); needs per-port DP config + wire format. The escape hatch for legitimate DNS/NTP upstreams is the existing whitelist/VIP (BLK-26), not this feature. |
| Port **ranges** and **auto-expiry** on entries | Q3 option (b), **not chosen** (D-AMP-3). v1 = single port + optional note, no expiry. A temporary-block/expiry sweep is a deferred idea. |
| Changing the **hardcoded** amplification set (17,19,53,111,123,137,161,389,520,1900,5353,11211) | Compile-time constant, owned by BLK (D-BLK-1). This tab displays it read-only; changing it is a data-plane rebuild, not config. |
| Whitelisting legitimate upstream sources (resolver/NTP) so their UDP responses aren't amp-dropped | Already owned by **Whitelist/VIP** (BLK-26 onboarding guidance). This tab may *link* to it but does not reimplement it. |
| Bogon-source policy, blacklist CRUD, per-service rate-limits | Other features (BLK bogon = compile-time; SRL/global-blacklist; service-ratelimit). The DDoS Protection tab may host them **later**; v1 = UDP amplification only. |
| Auto-response / one-click mitigate (auto-populate blocked ports from attack telemetry) | GA deferred idea OP-02. This feature is manual admin config only (v1 project constraint: no auto-rule generation). |
| Extending the apply-snapshot wire format to carry the port list | Q2 option (b), **not chosen** (D-AMP-2). The reconcile lane writes the map directly. |
| A new drop reason or a new `dpstat` **counters** surface for dynamic-vs-hardcoded attribution | Both hit `udp_amplification_drop` (index 7) indistinguishably by design (BLK edge cases). Effective-state read-back is P2 (AMP-18), reusing existing `dpstat snapshot`. |

---

## User Stories

### P1: Admin CRUD for the dynamic blocked-port list (model + API) ⭐ MVP

**User Story**: As a platform admin, I want to add and remove UDP source ports on a node-global block
list from the API, so that I can respond to a novel reflection vector during the pilot without a
data-plane rebuild.

**Why P1**: This is the deferred D-BLK-2 writer — the whole feature is pointless without a persistent,
audited desired-state store the reconcile lane can read.

**Acceptance Criteria**:

1. WHEN the schema is defined THEN there SHALL be a **node-global** desired-state store for dynamic
   blocked UDP source ports — one row per port, `port` an integer in **0..65535** with a uniqueness
   constraint, an optional bounded free-text `note`, and creation provenance (`created_by`,
   `created_at`); there is **no** tenant or service dimension (A-BLK-7). `(AMP-01)`
2. WHEN an admin POSTs a new port THEN the system SHALL validate `0 ≤ port ≤ 65535` (422 on out of
   range / non-integer), reject a duplicate with **409** (idempotency is the caller's choice, not a
   silent no-op), bound the optional `note` length (422 if over), persist the row, and continue to
   AMP-08 propagation. `(AMP-02)`
3. WHEN an admin DELETEs a port THEN the system SHALL remove the row and drive propagation; a delete
   of an absent port SHALL return **404** (distinguishable from success). `(AMP-03)`
4. WHEN an admin GETs the list THEN the system SHALL return all dynamic entries (newest-first or
   port-ordered — Design), **plus** the read-only built-in set (AMP-05) in the same or a sibling
   payload so the UI renders both without a second contract. `(AMP-04)`
5. WHEN the list is read THEN the response SHALL include the compile-time **hardcoded** amplification
   source-port set the data plane always drops (17, 19, 53, 111, 123, 137, 161, 389, 520, 1900, 5353,
   11211), marked read-only; the control-plane's copy of this set SHALL be a **single documented
   constant** with an explicit note that the data-plane header (`amp_port_hardcoded`) is authoritative
   and the two are changed together (A-AMP-4). `(AMP-05)`
6. WHEN any AMP endpoint is called by a non-admin (tenant user) THEN it SHALL return **403** — this is
   node-global config, admin-only by structure (mirrors Threat Feeds / Global Blacklist / Node
   Control; §5.2). `(AMP-06)`
7. WHEN a port is created or deleted THEN the system SHALL write an `AuditEvent` via `record_event`
   with the dangerous-action taxonomy and `scrub_metadata` (mirroring `feed.*` / `blacklist.*` /
   `node.*` writes) — actor, action, port, and note captured. `(AMP-07)`

**Independent Test**: As admin, POST port 3702 with note "WS-Discovery" → 201; POST 3702 again → 409;
GET → list contains 3702 and the 12-entry read-only built-in set; DELETE 3702 → 204/200, GET no longer
lists it; DELETE 3702 again → 404; POST 70000 → 422; the same calls as a tenant user → 403; an
`AuditEvent` row exists for each successful create/delete.

---

### P1: Propagation to the data-plane bitmap (worker lane + `dpstat`) ⭐ MVP

**User Story**: As the gateway operator, I want a port I block in the API to actually stop matching
UDP traffic within seconds and stay blocked across config applies and restarts, so the control surface
is real and not just a database table.

**Why P1**: Without propagation the CRUD is inert. Doing it as a background reconcile lane (not a
wire-format change) is D-AMP-2 and keeps node-global config decoupled from per-service applies.

**Acceptance Criteria**:

1. WHEN the desired port set in Postgres differs from what the data plane enforces THEN a **worker
   background reconcile lane** (background asyncio task, not a Redis `JobType`; ~1 s tick, mirroring
   `NodeControlReconciler` / `NextHopResolver`) SHALL drive the `udp_blocked_port_bitmap` to match the
   desired set within the reconcile SLA (target ≤ 5 s), and SHALL re-assert on worker restart (PG is
   the source of truth). `(AMP-08)`
2. WHEN the lane writes the map THEN it SHALL do so through a **new privileged `dpstat` subcommand**
   (e.g. `dpstat set-blocked-ports <p1,p2,...>`) that computes the full 1024-word bitmap for the
   desired set and writes it to the pinned `udp_blocked_port_bitmap`; the worker SHALL exec it
   unprivileged exactly as `DpstatBypassWriter` execs `set-bypass` (privilege isolated in the binary).
   `(AMP-09)`
3. WHEN the `dpstat` write fails (nonzero exit, binary missing, map unpinned) THEN the lane SHALL
   **fail safe** — leave the last-good bitmap in place (never clear-on-error), log/surface the error,
   and retry on the next tick; a transient failure SHALL NOT open a previously blocked port.
   `(AMP-10)`
4. WHEN an unrelated **service or feed apply** runs (which rebuilds/flips the double-buffer slots and
   `apply_copy_outer_inner`-carries the bitmap forward) THEN admin-configured ports SHALL remain
   enforced afterward — the writer SHALL populate the map so it is **carry-forward-safe** (Design
   decision: write both slots, or write the active slot relying on carry-forward — the spec requires
   only the post-apply invariant that no configured port is dropped). `(AMP-11)`
5. WHEN a port set becomes enforced THEN a matching **UDP** packet whose **source port** is in the set
   SHALL drop `udp_amplification_drop` (frozen ABI index 7) via the existing BLK stage — no new stage,
   no new reason, no hot-path change; a **TCP** packet from the same source port SHALL be unaffected
   (BLK-14). `(AMP-12)`
6. WHEN the feature is delivered THEN it SHALL add **no** apply-snapshot wire-format change, **no** new
   `JobType`, and **no** new drop reason (D-AMP-2); the reconcile lane and `dpstat` subcommand are the
   only new propagation code. `(AMP-13)`

**Independent Test**: With the gateway loaded, POST port 9999 → within the SLA a live `dpstat`
snapshot shows bit 9999 set and a `BPF_PROG_TEST_RUN` / live UDP src-port-9999 packet drops index 7,
src-port 9998 passes; trigger a service apply → 9999 still drops afterward; stop and restart the
worker with 9999 still in PG → 9999 remains enforced; simulate a `dpstat` failure → the previously
blocked 9999 stays blocked and the error is logged.

---

### P1: DDoS Protection admin dashboard tab ⭐ MVP

**User Story**: As a platform admin, I want a **DDoS Protection** tab in the dashboard that shows the
always-on built-in amplification ports and lets me add/remove dynamic blocked ports, so I can manage
UDP amplification protection without the API.

**Why P1**: The request is explicitly "add a DDoS Protection tab" — the UI is the deliverable's face;
the CRUD/propagation is what it drives.

**Acceptance Criteria**:

1. WHEN an **admin** loads the dashboard THEN a role-filtered sidebar item **"DDoS Protection"** SHALL
   appear (under *Manage*, alongside Threat Feeds / Global Blacklist / Node Control) routing to a new
   admin-only page; a **tenant user** SHALL NOT see it (mirrors the existing role-filtered nav).
   `(AMP-14)`
2. WHEN the page renders THEN it SHALL show a **read-only** "Built-in blocked source ports (always
   on)" section listing the 12 hardcoded ports (from AMP-05), and a **"Dynamic blocked source ports"**
   section listing the admin-managed entries (port + note) with add and remove controls. `(AMP-15)`
3. WHEN the admin adds a port THEN the form SHALL validate the port client-side (0..65535, integer),
   accept an optional note, submit, and surface server `422`/`409` via the existing
   `fieldErrorsFrom422` / `{detail}` pattern (e.g. "port already blocked" on 409). `(AMP-16)`
4. WHEN the admin removes a port THEN the UI SHALL confirm (ConfirmDialog), call DELETE, and
   optimistically/refetch-update the list (TanStack Query invalidation). `(AMP-17)`
5. WHEN a mutation succeeds THEN the UI SHALL communicate that enforcement is applied by the
   background lane — a lightweight toast/hint ("Blocked-port list updated; applying to data-plane"),
   **not** a per-service apply-status indicator (there is no service apply here). `(AMP-18)`
6. WHEN the page is covered by tests THEN Vitest specs (mocked `apiClient` + `QueryClient`) SHALL
   assert list rendering (built-in + dynamic), add success + 409 error surfacing, remove-with-confirm,
   and admin-only gating; the fe gate (`lint && typecheck && test --run && build`) SHALL pass.
   `(AMP-19)`

**Independent Test**: Log in as admin → "DDoS Protection" appears in the sidebar → the page shows the
12 built-in ports read-only and the dynamic list; add 27015 → appears in the list, backend 201; add
27015 again → inline "already blocked" (409); remove 27015 with confirm → gone; log in as a tenant
user → no sidebar item and the route is not reachable; `npm run` fe gate green.

---

### P2: Effective-state read-back (confirmation & drops)

**User Story**: As an admin, I want to confirm the ports I configured are actually enforced and see
whether they're catching traffic, so I trust the tab reflects the live data plane.

**Why P2**: Useful assurance and closes the desired-vs-effective loop, but the P1 slice is complete
and demoable without it; it reuses existing telemetry/`dpstat` surfaces.

**Acceptance Criteria**:

1. WHEN the page loads THEN it MAY show the **effective** blocked-port set read from the live data
   plane (reusing `dpstat snapshot` / a node-health field) so desired-vs-effective drift is visible
   during propagation. `(AMP-20)`
2. WHEN amplification drops occur THEN the page MAY surface the live `udp_amplification_drop` counter
   (reusing the existing telemetry/node-health reader) so the admin sees the filter working;
   dynamic-vs-hardcoded attribution is **not** distinguished (same counter by design). `(AMP-21)`

**Independent Test**: Configure port 9999, generate UDP src-port-9999 traffic → the page shows 9999 in
the effective set and a rising `udp_amplification_drop` count.

---

## Edge Cases

- WHEN a port that is already in the **hardcoded** set (e.g. 53) is added to the dynamic list THEN the
  API SHALL accept it (it is a legal dynamic entry) but the UI SHOULD note it is already always-on;
  enforcement is unchanged (hardcoded check fires first — indistinguishable in counters, BLK edge
  case). Design MAY choose to warn rather than reject (A-AMP-5).
- WHEN the desired list is **empty** THEN the lane SHALL write an all-zero dynamic bitmap (no dynamic
  blocking); the hardcoded set still applies. An empty list is a valid state, never an error.
- WHEN the gateway/BPF map is **not loaded** (pins absent) THEN the CRUD still succeeds (PG is desired
  state) and the lane's `dpstat` write fails safe (AMP-10) until the map exists — the same posture as
  `set-bypass` against an unloaded gateway (friendly error, retried).
- WHEN two admins edit concurrently THEN last-write-wins at the row level (unique `port` prevents
  duplicates); the lane always reconciles to the current full PG set (no partial state).
- WHEN a config apply flips the double-buffer slot mid-reconcile THEN the next tick re-asserts;
  because the writer is carry-forward-safe (AMP-11), no configured port is transiently opened.
- WHEN `note` contains secrets or PII THEN `scrub_metadata` on the audit path applies; the note is
  operator free-text and bounded (AMP-01/02) — no validation of content beyond length.
- WHEN a port is added and immediately removed before a tick THEN the lane reconciles to the final PG
  state (removed) — intermediate states are not guaranteed to reach the map (eventual consistency).
- WHEN the worker is down THEN CRUD still persists; enforcement converges when the worker returns
  (AMP-08 re-assert), same availability posture as every other reconcile lane.

---

## Gray Areas (RESOLVED — see `context.md`, D-AMP-1..3)

**D-AMP-1 → scope = dynamic port list + read-only built-ins** (Q1 option a). Enforcement stays
always-on; **no** node-wide toggle, **no** per-built-in override (both moved to Out of Scope /
deferred ideas).

**D-AMP-2 → propagation = worker reconcile lane + `dpstat set-blocked-ports`** (Q2 option a). No
apply-snapshot wire-format change; mirrors `NodeControlReconciler` / `NextHopResolver`. Decouples
node-global config from per-service applies and avoids stacking on the in-flight service-ratelimit
wire v3 bump.

**D-AMP-3 → entry model = single port + optional note** (Q3 option a). Node-global, admin-only, no
expiry, no ranges.

---

## Assumptions (flagged, not user-blocking)

- **A-AMP-1:** New CP model `BlockedUdpPort` (or similarly named) + Alembic migration, number pinned
  **live at Execute** (head today is `20260714_0011_alerting`; SLA/OLA also plans `_0012`, so the
  actual number is assigned at Execute against the real head). Additive; no change to existing tables.
- **A-AMP-2:** The reconcile lane is a new `worker/` module (e.g. `blocked_port_reconciler.py`) with
  a `Dpstat…Writer` + `…Reconciler` pair wired in `worker/worker.py` beside the bypass/next-hop lanes,
  with a request-event to jump the tick on a CRUD mutation (mirroring the bypass `_reconcile_requested`
  event) — the exact fold vs. new-lane and settings (`worker_blocked_port_*`) are Design.
- **A-AMP-3:** `dpstat set-blocked-ports` writes **both** double-buffer slots' inner maps with the
  full desired bitmap (idempotent full-set write), making it carry-forward-safe without depending on
  apply timing; whether to instead write only the active slot is a Design call weighed against the
  flip race (AMP-11 states only the invariant).
- **A-AMP-4:** The read-only built-in set is exposed to the UI via a **CP constant mirroring the DP
  `amp_port_hardcoded` switch**, documented as "DP header is authoritative; keep in sync." A live
  read of the set from `dpstat` is possible but the set is compile-time and the mirror is trivial;
  drift risk is a documented single-source caveat (candidate P3: derive from a shared generated
  header).
- **A-AMP-5:** Adding a port already in the hardcoded set is **accepted with a UI note** (not
  rejected) — the dynamic list is a superset the operator controls; forbidding overlap would couple
  the CP to the DP set semantics. Design may choose a soft warning.
- **A-AMP-6:** API router prefix/shape (e.g. `/ddos/amplification/ports` or `/node/amplification`)
  and SPA route (e.g. `/admin/ddos`) are Design/naming calls; the spec fixes only the capability,
  admin-only scope, and audit, not the exact path.
- **A-AMP-7:** No hot-path/data-plane C change beyond the `dpstat` subcommand + its build; the loader
  env seed (`XDPGW_SEED_BLOCKED_PORT`) remains as a bootstrap and is not removed (it seeds slot 0 at
  load; the lane converges the runtime set thereafter).
- **A-AMP-8:** Propagation SLA target is "≤ 5 s" to match PRD config-propagation, but this path is a
  ~1 s reconcile tick + a fast `dpstat` write; it is measured/asserted in the live smoke, not on the
  hot path.

---

## Amendment A1 — Unified "UDP reflection & amplification" section (2026-07-23)

**Trigger:** post-delivery UX review of the shipped tab. AMP-15 delivered the built-in set and the
dynamic set as **two visually separate blocks** (a chip Card "Built-in blocked source ports (always
on)" + a `DataTable` "Dynamic blocked source ports"), on a page that *also* carries a
`ProtectionCoverage` card titled "UDP reflection & amplification". An admin therefore reads the same
vector described in three places and has to mentally union two lists to answer the only question that
matters: **"which UDP source ports are blocked on this node right now?"**

**Change:** merge the two blocks into **one section headed "UDP reflection & amplification"** whose
subtitle is "Dynamic blocked source ports", backed by a **single table**. Built-in ports render first
as **locked rows** (badged `Built-in`, no Remove action); admin-managed entries follow with Remove.
This is a **presentation-only** change: no API contract, no `AmplificationConfigResponse` shape
change, no worker/data-plane change — the page keeps consuming `hardcoded_ports` + `dynamic_ports`
from the same `useAmplificationConfig` query.

**Decisions (this amendment, resolved via AskUserQuestion 2026-07-23):**

- **D-AMP-4 → merge shape = one table, built-ins as locked rows** (chosen over "chips above the
  table" and "source column + filter toggle"). One list is one mental model; the filter variant was
  rejected as extra state and test surface for a 12-row constant.
- **D-AMP-5 → `ProtectionCoverage` stays unchanged.** It is **shared with the tenant-facing
  `DdosCoveragePage`**, so hiding or retitling its amplification card to de-duplicate the admin page
  would change what tenants see. The residual duplication (a one-line summary card above a detailed
  section) is accepted as summary-then-detail, not redundancy.

**Requirements:**

1. WHEN an admin loads the DDoS Protection page THEN the built-in and dynamic blocked source ports
   SHALL appear in **one section** headed "UDP reflection & amplification" / "Dynamic blocked source
   ports", rendered by a **single table**; the standalone "Built-in blocked source ports (always on)"
   Card SHALL be gone. This **supersedes AMP-15's** two-section layout. `(AMP-22)`
2. WHEN the merged table renders THEN each built-in port SHALL appear as a row marked with a
   read-only `Built-in` badge and a human-readable reflector label (DNS, NTP, SSDP, memcached, …),
   SHALL expose **no** Remove action, and SHALL sort before the admin-managed rows; each dynamic
   entry SHALL keep its note, blocked-at timestamp and Remove action. `(AMP-23)`
3. WHEN the dynamic list is empty THEN the section SHALL still list the built-in rows (so the
   `DataTable` empty state never fires) and SHALL surface an inline hint that no custom ports are
   blocked, with the add affordance still reachable. `(AMP-24)`
4. WHEN this amendment ships THEN `ProtectionCoverage` and `DdosCoveragePage` SHALL be
   **byte-unchanged** (D-AMP-5), and no control-plane, worker, or data-plane file SHALL change —
   the diff is the admin page component plus its Vitest spec. `(AMP-25)`

**Independent Test**: As admin, load the page → exactly one "UDP reflection & amplification" heading
over one table containing 12 `Built-in`-badged rows (UDP/17…UDP/11211, no Remove) followed by the
dynamic entries (with Remove); the "Built-in blocked source ports (always on)" heading is absent;
add/409/remove-with-confirm flows behave exactly as before; the tenant `DdosCoveragePage` renders
unchanged; fe gate (`lint && typecheck && test --run && build`) green.

---

## Requirement Traceability

| Requirement ID | Story | Refs | Phase | Status |
| --- | --- | --- | --- | --- |
| AMP-01 | P1: Admin CRUD | D-BLK-2, A-BLK-7, D-AMP-3 | CT1 | ✅ Verified |
| AMP-02 | P1: Admin CRUD | validation/409/audit | CT2/CT3 | ✅ Verified |
| AMP-03 | P1: Admin CRUD | delete/404 | CT2/CT3 | ✅ Verified |
| AMP-04 | P1: Admin CRUD | list contract | CT3 | ✅ Verified |
| AMP-05 | P1: Admin CRUD | hardcoded set, A-AMP-4 | CT2 | ✅ Verified |
| AMP-06 | P1: Admin CRUD | §5.2 admin-only, D-AMP-1 | CT3 | ✅ Verified |
| AMP-07 | P1: Admin CRUD | AuditEvent/record_event | CT2 | ✅ Verified |
| AMP-08 | P1: Propagation | reconcile lane, D-AMP-2 | CT4/CT5 | ✅ Verified |
| AMP-09 | P1: Propagation | dpstat set-blocked-ports, A-AMP-3 | CT4/DT1 | ✅ Verified |
| AMP-10 | P1: Propagation | fail-safe/retry | CT4 | ✅ Verified |
| AMP-11 | P1: Propagation | carry-forward-safe, M4 #2 | DT2 | ✅ Verified (smoke: survives slot-swap) |
| AMP-12 | P1: Propagation | BLK stage, ABI idx 7, BLK-14 | DT2 | ✅ Verified (smoke: idx 7 drop) |
| AMP-13 | P1: Propagation | no wire/JobType/reason change | (design) | ✅ Verified |
| AMP-14 | P1: SPA tab | role-filtered nav | FT2 | ✅ Verified |
| AMP-15 | P1: SPA tab | built-in + dynamic sections | FT2 | ✅ Verified — layout superseded by AMP-22 (A1) |
| AMP-16 | P1: SPA tab | form/422/409 | FT2 | ✅ Verified |
| AMP-17 | P1: SPA tab | remove/confirm/invalidate | FT2 | ✅ Verified |
| AMP-18 | P1: SPA tab | applying-hint UX | FT2 | ✅ Verified |
| AMP-19 | P1: SPA tab | Vitest + fe gate | FT1/FT2 | ✅ Verified |
| AMP-20 | P2: Effective-state | dpstat snapshot read-back | PT1 | Deferred (P2) |
| AMP-21 | P2: Effective-state | udp_amplification_drop counter | PT2 | Deferred (P2) |
| AMP-22 | A1: Unified section | D-AMP-4, supersedes AMP-15 layout | A1 | ✅ Verified |
| AMP-23 | A1: Unified section | locked built-in rows + labels | A1 | ✅ Verified |
| AMP-24 | A1: Unified section | empty dynamic-list hint | A1 | ✅ Verified |
| AMP-25 | A1: Unified section | D-AMP-5, FE-only diff | A1 | ✅ Verified |

**ID format:** `AMP-[NUMBER]`. **Status:** Pending → In Design → In Tasks → Implementing → Verified.

**Coverage:** 25 total. P1 = AMP-01..19 **✅ all Verified** (executed 2026-07-22, commits
`181223a..80a67ff`; CP/FE/DP gates green + DT2 privileged smoke passed). P2 = AMP-20..21 **deferred**
(PT1/PT2 optional, not executed). A1 = AMP-22..25 **✅ all Verified** (executed 2026-07-23,
presentation-only; fe gate green — lint/typecheck clean, **223 tests / 51 files** (220→223), build ok;
diff = `DdosProtectionPage.tsx` + its spec only, `ProtectionCoverage.tsx` / `DdosCoveragePage.tsx`
byte-unchanged).

---

## Success Criteria

- [ ] An admin adds port 9999 in the DDoS Protection tab; within the reconcile SLA a live UDP
      src-port-9999 packet drops `udp_amplification_drop` while src-port 9998 passes; removing 9999
      restores it — all without a rebuild or a service re-apply.
- [ ] The blocked set survives a service/feed apply and a worker restart (Postgres-sourced,
      carry-forward-safe); a `dpstat` write failure never opens a previously blocked port.
- [ ] The tab shows the 12 built-in ports read-only and the dynamic list; add is validated
      (0..65535), duplicates are 409, remove confirms; tenant users see neither the nav item nor the
      route; all mutations are audited.
- [ ] Backend gate green (CP `pytest` — new model/API/lane tests), data-plane build green (`dpstat`
      + `set-blocked-ports` + its unit/smoke), fe gate green (new tab + tests); no drop-reason ABI
      change, no apply-snapshot wire change, no new `JobType`.
