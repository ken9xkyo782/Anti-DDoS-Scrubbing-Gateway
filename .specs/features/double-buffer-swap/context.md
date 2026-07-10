# Double-buffer Map Build/Swap — Context (Discuss output)

**Spec:** `.specs/features/double-buffer-swap/spec.md` (DBS-01..28)
**Captured:** 2026-07-10 (discuss within Specify)
**Status:** Ready for design

---

## Feature Boundary

The **authoritative writer** that fills the applier boundary M4 #1 left as a placeholder. Agent-worker
(AD-026/AD-027, Tasks APPROVED) shipped `handle_service_update(db, job, applier)` → an injected
`Applier` protocol whose v1 `PlaceholderApplier` reads config, logs, and succeeds — `active` means
"acknowledged by the worker", the data-plane is still driven by the loader's env seed (D-SLRD-1).
This feature swaps that **implementation** (not the boundary — D-AGW-1) for a `DoubleBufferApplier`
that makes a committed control-plane change actually reach the XDP hot path.

- **Owns:** the `DoubleBufferApplier`, a new **C apply-helper binary** that builds the inactive slot of
  every *slotted* config map + verifies it + performs the single atomic `active_config` flip, pinning
  the config maps in the loader so a separate process can reach them, and fail-closed rollback.
- **Calls, never reimplements:** the executed `mark_active`/`mark_failed`/`retry` (APLY-03) and the
  agent-worker's `process_job` two-transaction guard — unchanged. The applier is invoked exactly where
  `PlaceholderApplier` is today.
- **Does not own:** the worker loop / reconcile / orphan recovery (M4 #1, executed-to-be), the
  **content** of feed-owned global deny maps (M4 #3 *Threat feed sync* is their authoritative writer —
  this feature only *carries them forward* across a swap), telemetry (M5), bypass/maintenance-mode use
  of `active_config` (M6), new API endpoints.

**Depends on** agent-worker **executed first** (currently Tasks APPROVED, not yet executed) — this
feature's Execute is gated on it. Shares the data-plane track's headers (the M4 build contracts frozen
by AD-015/019/021/023/025).

---

## Implementation Decisions

Three gray areas on the Python-worker ↔ eBPF-map seam were resolved with the user after the spec draft
(2026-07-10).

### D-DBS-1: Write mechanism = a C apply-helper binary invoked by the worker

**Question:** How does the Python worker actually build & write the BPF config maps (create fresh
inner bloom/LPM maps per swap, populate ~11 slotted maps, flip `active_config`)?
**Decision:** The `DoubleBufferApplier.apply()` **shells out to a new C helper binary** (extend the
loader family / a new `tools/xdpgw-apply`) that opens the pinned config maps, builds the inactive slot,
structurally verifies it, and performs the atomic flip; it returns exit 0 on success, nonzero on any
failure. The Python boundary stays `Applier.apply(config)` (D-AGW-1 honored); the helper reuses the
loader's **proven** `seed_*` / inner-map-creation / `bpf_map_update_elem` routines.
**Why:** All the hard BPF-fu (fresh `ARRAY_OF_MAPS` inner creation for replace-only blooms + LPM tries,
map-in-map installs, the single-write flip) already exists and is verified in C (`loader/loader.c`,
`data-plane/tests`). Re-implementing inner-map creation in Python (pyroute2/libbpf-ctypes) duplicates
load-bearing, error-prone code across a language boundary; `bpftool` per-element is too clumsy for
map-in-map inner creation and slow at scale. The subprocess keeps every kernel interaction in one
audited C surface, testable standalone via `BPF_PROG_TEST_RUN`.
**Trade-off:** A process boundary (serialize node config in → exit-code + stderr out) instead of an
in-process call; the helper needs the config maps **pinned** (they are not today — only observability
maps are, per `loader.c`). "Worker needs bpffs from this process" (A-AGW-1) is satisfied via the
child process it spawns, colocated on the node.
**Impact:** `DBS-06..10`; new `tools/xdpgw-apply` (or loader `apply` mode) + loader change to pin the
slotted config maps + `active_config`; the applier's job = marshal input, exec, interpret exit code.

### D-DBS-2: Every job rebuilds the full node slot; feed-owned global deny maps are carried forward

**Question:** A `SERVICE_UPDATE` job targets one service at version N, but a slot flip is node-global.
What goes into the inactive slot per job?
**Decision:** **Rebuild the entire inactive slot from all active services' current DB config** every job
(service_map + rule_block + whitelist/vip + service_blacklist + fair_config for *every* enabled
service), verify, single flip. The **feed-owned global deny maps** (`global_blacklist_bloom/lpm`,
`udp_blocked_port_bitmap`, `gbl_meta`) are **carried forward** — reinstalled/preserved into the new
slot from the currently-live slot — so a per-service swap never drops the global blacklist (whose
authoritative writer is M4 #3). On flip, all services in the slot go live together; `mark_active`
advances only the **triggering** service's per-service `active_version` (the state machine stays
per-service; the slot is the physical carrier). Both slots converge over successive applies.
**Why:** Matches the ROADMAP contract ("Build full inactive slot, verify, then single `active_slot`
write; rollback = flip back"). A full rebuild is self-consistent and stateless (no slot-to-slot copy of
map-in-map inners to keep in sync); the incremental alternative must reliably clone the active slot
(incl. blooms that are **replace-only** and cannot be cleared/diffed) before overwriting one service —
strictly more fragile for no correctness gain in v1.
**Trade-off:** O(all active services) build work per job (must fit the ≤5 s budget — DBS-25/26; the 1M
global blacklist is *not* rebuilt per job, it is carried forward, which keeps the cost bounded to
service-scoped maps). Rebuilding all services on every single-service change is redundant work, accepted
for simplicity at the ≤100-tenant/1000-service envelope; batch-coalescing is a deferred optimization.
**Impact:** `DBS-15..18`; the helper's input is the **full node config**, not one `ServiceConfig`
(Design resolves: worker serializes the node snapshot to the helper vs the helper reads PG directly —
A-DBS-2); carry-forward mechanics for feed maps = a Design detail (reinstall same inner fd vs re-read).

### D-DBS-3: Verify = structural read-back before the flip

**Question:** What does "verify the built inactive slot" mean — the gate that decides swap vs rollback?
**Decision:** **Structural read-back.** After building, the helper reads back key invariants from the
inactive slot before flipping: every enabled service present in `service_map[inactive]`, each service's
`rule_block[inactive]` version/count matching what was built, every per-slot inner map installed
(bloom/LPM/vip/fair/bitmap fds non-null), `fair_node_config`/`gbl_meta` slot rows coherent. Any mismatch
→ abort, **no flip**, exit nonzero → `mark_failed` (active_version kept). No live traffic is involved in
the gate.
**Why:** Catches the realistic failure modes of a build (a dropped `bpf_map_update_elem`, a failed
inner-map create/install, a truncated node snapshot) deterministically and offline, without the
complexity/flakiness of a live `BPF_PROG_TEST_RUN` probe or the false confidence of "every write
returned 0" alone. Live-probe verification is a stronger but heavier option deferred to a later
hardening pass.
**Trade-off:** Read-back is O(services) extra map reads per apply (cheap vs the build); it validates
*structure*, not *semantics* — a structurally-correct but policy-wrong build (a control-plane bug) is
not caught here (that is the control-plane's own test surface, not the swap gate).
**Impact:** `DBS-11..13/18`; the helper gains a `verify_slot(inactive)` pass between build and flip; the
rollback path is "abort before flip", never "flip then flip back" (nothing became live).

---

## Flagged assumptions (written into spec, confirm during Design)

- **A-DBS-1 — Boundary unchanged, impl swapped.** `handle_service_update` keeps calling
  `applier.apply(cfg)`; `DoubleBufferApplier` replaces `PlaceholderApplier` at the injection site
  (`__main__` / DI). The single `ServiceConfig` identifies the triggering service + version (for the
  log + supersede check); the full-node rebuild reads the rest of the node's config. No new `JobType`
  (A-AGW-4 preserved) — the full-node-rebuild-per-`SERVICE_UPDATE` subsumes any need for standalone
  `MAP_REBUILD`/`ACTIVE_SLOT_SWAP` job types in v1 (those stay deferred despite AD-027's mention).
- **A-DBS-2 — Helper input channel.** The helper needs the full node config. Proposed: the worker
  serializes a full-node snapshot (all active services + plan/rules/lists) and passes it to the helper
  (stdin/temp file), keeping all PostgreSQL access in one language and the helper DB-free + fixture-
  testable. Alternative: the helper reads PG directly (libpq). Design decides.
- **A-DBS-3 — Config-map pinning is additive to the loader.** The loader gains pins for the ~11 slotted
  config maps + `active_config` under `/sys/fs/bpf/xdp_gateway/`, alongside the existing observability
  pins; the seed helpers still run at load to establish an initial coherent slot (baseline/smoke
  unchanged). The helper opens maps by pin.
- **A-DBS-4 — Carry-forward preserves the live global deny maps.** Until M4 #3 lands, the global
  blacklist/bitmap content is whatever the loader env seed established; the swap must not lose it. In
  v1 the simplest correct carry-forward (reinstall the live slot's global inner maps into the new slot,
  or skip rebuilding them so both slots share content) is a Design call.
- **A-DBS-5 — active_config.version is the node-global map version** (`{active_slot, version}`), bumped
  per successful swap; it is distinct from each service's per-service `active_version` advanced by
  `mark_active`. dpstat/telemetry surface it (map version, PRD §telemetry).
- **A-DBS-6 — No unsolicited startup swap** (AGW-21, binding on this feature): the applier never flips
  on startup; only a job drives a swap. The loader seed owns the initial slot.
- **A-DBS-7 — ≤5 s re-validated here** with real map builds (A-AGW-7); the scale-envelope full-rebuild
  cost is measured at a gated check, not asserted a priori.
- **A-DBS-8 — Single writer.** One worker, one apply at a time (A-AGW-2); the helper assumes no
  concurrent writer to the config maps. A stray second writer is unsupported (guards keep state safe,
  not the physical slot build).

---

## Specific References

- **D-AGW-1** (v1 placeholder applier; "M4 #2 replaces the applier *implementation*, not the boundary,
  and re-validates the ≤5 s bound with real map builds") — the exact contract this feature fulfils.
- **AD-027** (Applier boundary + `HANDLERS` registry + `process_job`/`reconcile_once`; "M4 #2 =
  DoubleBufferApplier") — the seam this plugs into, unmodified.
- **D-SLRD-1** (interim seed-helper writer; "authoritative build = M4") — this **is** that authoritative
  build; the loader env seed downgrades to initial-slot bootstrap only.
- **AD-015** (`service_map` = `ARRAY_OF_MAPS[2]` of LPM + `active_config` + `tx_devmap`; hot path reads
  `active_slot` at ingress) — the swap point.
- **AD-019/021/023/025** — the frozen M4 build contracts for `rule_block`, whitelist/VIP, blacklist,
  and fairness maps respectively (`rules.h`/`whitelist.h`/`blacklist.h`/`fairness.h`): "M4 builds a
  fresh inner per swap", pre-sorted rule blocks, replace-only blooms, precomputed fairness budgets.
- **AD-017** (bpffs pin pattern "the M4 worker will reuse"; observability maps pinned under
  `/sys/fs/bpf/xdp_gateway/`) — the pattern extended to config maps here.
- **§8.3** (slotted config maps vs unslotted runtime-state maps) — the swap touches only the former.
- **PRD 6.8 / 11.3 / TDD 4.5** (swap-only-on-full-build, restart preserves active state, worker
  sequence: read full config → build → swap/fail, ≤5 s).

---

## Deferred Ideas

- **Live-probe verification** (`BPF_PROG_TEST_RUN` against the inactive slot before flip) — a stronger
  verify gate; D-DBS-3 chose structural read-back for v1.
- **Standalone `MAP_REBUILD` / `ACTIVE_SLOT_SWAP` job types** — subsumed by full-node-rebuild-per-
  `SERVICE_UPDATE` in v1 (A-DBS-1); revisit if an admin-triggered full re-seed is wanted.
- **Batch coalescing** — skip to the newest queued version per service and rebuild once for a burst of
  edits (pairs with the agent-worker's deferred backpressure idea) — optimization under sustained churn.
- **Incremental slot copy** — clone-active-then-overwrite-one-service (rejected for v1 by D-DBS-2);
  only worth it if the full-rebuild cost breaches ≤5 s at a larger envelope.
- **One-click rollback to previous version (OP-05, GA)** — this feature's flip-back is the mechanism;
  the user-facing "roll back to version K" surface is GA.
