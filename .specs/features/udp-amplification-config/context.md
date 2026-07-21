# UDP Amplification Config — Discuss Context

Gray areas resolved with the user via AskUserQuestion (2026-07-21), before writing `spec.md`. All
three took the **recommended** option. These decisions bind the spec (AMP-01..21).

---

## D-AMP-1 — v1 scope of the DDoS Protection tab

**Question:** What should the tab let an admin manage for UDP amplification in v1?

**Decision (option a):** **Dynamic blocked source-port list CRUD + read-only view of the built-in
set.** Enforcement stays always-on.

**Rejected:**
- (b) node-wide on/off toggle — would add a new data-plane runtime flag + hot-path branch for an
  emergency kill-switch. **Deferred idea** (STATE.md).
- (c) per-built-in-port override — contradicts the PRD's "hardcoded" intent (GA-1c from
  blacklist-filters), needs per-port DP config + wire format; the principled escape hatch for
  legitimate DNS/NTP upstreams is the existing whitelist/VIP (BLK-26), not this feature.

**Reason:** The user's request ("Quản lý cấu hình UDP amplification") maps cleanly to the deferred
D-BLK-2 writer — the one genuinely missing surface. The hardcoded set is compile-time by design;
surfacing it read-only explains the always-on posture without inventing config the PRD deliberately
omitted.

**Impact:** P1 = AMP-01..19 (CRUD + propagation + tab). Toggle and per-port override → Out of Scope
+ deferred ideas.

---

## D-AMP-2 — how blocked ports reach the data-plane bitmap

**Question:** How should admin-configured ports reach `udp_blocked_port_bitmap`?

**Decision (option a):** **A new worker reconcile lane + a `dpstat set-blocked-ports` subcommand.**
The lane (background asyncio task, ~1 s tick, not a Redis `JobType`) computes drift between the
Postgres desired set and the live map and execs the privileged `dpstat` subcommand to write the map;
the worker execs it unprivileged. This is a 1:1 clone of the **present-in-code** precedent:
`NodeControlReconciler` + `DpstatBypassWriter` (`set-bypass`) and `NextHopResolver` +
`DpstatNextHopWriter` (`set-nexthop`).

**Rejected:**
- (b) extend the apply-snapshot wire format + rebuild the bitmap from Postgres in `DoubleBufferApplier`
  on each apply — atomic with config swaps, but bumps the wire format (**mid-flight at v3** for the
  in-progress service-ratelimit feature — would stack v3→v4), couples node-global config to
  per-service applies, and rebuilds a node-global map on every service change.

**Reason:** The bitmap is **node-global** (A-BLK-7), so it does not belong in a per-service apply
snapshot; the reconcile-lane pattern is proven, decoupled, restart-safe, and avoids touching the
double-buffer wire format while the service-ratelimit v3 bump is in flight. `xdpgw-apply` already
**carries the bitmap forward** on applies, so a direct writer + carry-forward keeps ports enforced
across swaps.

**Impact:** AMP-08..13. No apply-snapshot wire change, no new `JobType`, no drop reason. New
`worker/*_reconciler` module + `dpstat set-blocked-ports`. Carry-forward-safety (write both slots vs
active-only) is a Design detail (A-AMP-3) — the spec fixes only the post-apply invariant (AMP-11).

---

## D-AMP-3 — dynamic blocked-port entry model

**Question:** What shape should a dynamic blocked-port entry take?

**Decision (option a):** **Single UDP source port (0..65535) + optional note.** Node-global,
admin-only, no expiry, no ranges.

**Rejected:**
- (b) port/range + expiry (auto-remove) — adds range expansion + an expiry sweep; a temporary-block
  workflow is a **deferred idea**.
- (c) bare port, no metadata — loses the audit/why context a `note` gives operators.

**Reason:** The map is a flat node-global bitmap of ports; a per-port row with an optional note is the
minimal model that keeps operator intent recorded and audited. Ranges/expiry are speculative for the
pilot and can be layered later without a schema break (add columns).

**Impact:** AMP-01 (`port` unique 0..65535, optional bounded `note`, `created_by`/`created_at`).

---

## Assumptions carried into the spec (A-AMP-1..8)

See `spec.md` "Assumptions" — model+migration number pinned live at Execute (head
`20260714_0011_alerting`); new `worker/` reconcile lane mirroring bypass/next-hop with a
jump-the-tick request event; `dpstat set-blocked-ports` writes both slots (carry-forward-safe); the
read-only built-in set is a CP constant mirroring the authoritative DP `amp_port_hardcoded` switch;
overlap with the hardcoded set is accepted-with-a-note; router/route names are Design calls; the
loader env seed stays as a bootstrap; the ≤5 s SLA is measured in the live smoke, not on the hot path.

---

## Grounding verified live in-tree (2026-07-21)

- **Enforcement + map:** `data-plane/src/blacklist.h` — `amp_port_hardcoded` (12-port switch: 17, 19,
  53, 111, 123, 137, 161, 389, 520, 1900, 5353, 11211), `udp_blocked_port_bitmap`
  `ARRAY_OF_MAPS[SERVICE_SLOTS=2]` of inner `ARRAY[BLOCKED_PORT_WORDS=1024]×u64`; both drop
  `DR_UDP_AMPLIFICATION_DROP = 7`.
- **Write idiom:** `loader.c::seed_blocked_port_from_env` — `key = port>>6`, `bit = 1<<(port&63)`,
  read-modify-write the u64 word (seeds inner slot 0 only, from `XDPGW_SEED_BLOCKED_PORT`).
- **Carry-forward:** `data-plane/tools/xdpgw-apply.c` — `carry_forward_feed` /
  `carry_forward_service_config` both `apply_copy_outer_inner(udp_blocked_port_bitmap_fd, active,
  inactive)`; the bitmap is never rebuilt from the apply snapshot.
- **Precedent (present in code, the template to clone):** `dpstat.c` subcommands `set-bypass` /
  `set-nexthop`; `worker/node_control_reconciler.py` (`DpstatBypassWriter` + `NodeControlReconciler`,
  `reconcile_once`, restart re-assert); `worker/nexthop_resolver.py` (`NextHopResolver` +
  `DpstatNextHopWriter`); `worker/worker.py` wires both lanes with a `_reconcile_requested` jump-tick
  event.
- **CP surface reuse:** `require_admin`, `AuditEvent`/`record_event`/`scrub_metadata`; admin-only
  node-global routers (feeds, global-blacklist, node) as the RBAC/audit template; migration head
  `20260714_0011_alerting`.
- **SPA reuse:** `control-plane/frontend/src/layout/Sidebar.tsx` (role-filtered *Manage* group:
  Threat Feeds, Global Blacklist, Node Control), `src/features/config/*` page pattern, `ui/`
  primitives, `apiClient` `{detail}` + `fieldErrorsFrom422`, TanStack Query.
- **No control-plane model/API/CRUD for blocked ports exists** (confirmed by grep) — this is the
  deferred D-BLK-2 writer.
