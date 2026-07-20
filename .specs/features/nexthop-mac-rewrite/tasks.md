# Static Next-Hop L2 (MAC) Rewrite — Tasks

**Design:** `.specs/features/nexthop-mac-rewrite/design.md` (AD-035)
**Spec:** `.specs/features/nexthop-mac-rewrite/spec.md` (NHR-01..21)
**Status:** Draft (awaiting approval → Execute)

**Baselines pinned live at Execute:** `B_dp` = current `data-plane/ make test` head (TESTING.md records 130; AD-DP-01's +3 is uncommitted — pin the real head at Execute). `B_cp` = current `control-plane/ pytest -q` head. Each task states its added-test floor and keeps the total monotonic.

**Tracks:** **DP** (`data-plane/`, C/XDP) ∥ **CP** (`control-plane/`, Python). The two tracks run **concurrently** (independent toolchains + test infra; CP tests use fakes, no dpstat). Serial **within** each track: DP on shared C files + the privileged smoke; CP on the shared `compose.test.yml` (integration = not parallel-safe). Only **CT6 (docs)** is `[P]`.

---

## Execution Plan

```
DP track (serial: shared C files, smoke):
    DT1 ──► DT2 ──► DT3

CP track (serial: shared compose.test.yml — integration bottleneck):
    CT1 ──► CT2 ──► CT3 ──► CT4 ──► CT5

Cross-track: DP ∥ CP (concurrent). CT6 [P] docs — anytime after its subjects land.
No hard cross-track dependency: CP tests use FakeNextHopWriter / fake snapshot;
the DP↔CP contract (dpstat subcommand args + snapshot "nexthop" JSON) is fixed in AD-035.
```

Phase view:
- **Phase 1 (concurrent):** `DT1` (DP core) ∥ `CT1` (single-host validation)
- **Phase 2 (concurrent):** `DT2` (loader/dpstat) ∥ `CT2` (resolver lane)
- **Phase 3 (concurrent):** `DT3` (veth smoke) ∥ `CT3` (worker wiring + post-apply hook)
- **Phase 4 (CP tail):** `CT4` (P2 /node/health) → `CT5` (P3 manual resolve + metrics)
- **`CT6` [P] docs** — after DT1/DT2 + CT1 land.

---

## Task Breakdown

### DT1: Next-hop map + fail-closed hot-path rewrite

**What:** Add `nexthop.h` (map + value + `nexthop_rewrite` helper), append the drop-reason ABI, rewire `redirect_out` to resolve-first/fail-closed, delete the `bpf_fib_lookup` path, make bypass verbatim.
**Where:** `data-plane/src/nexthop.h` (new); `data-plane/src/drop_reason.h`, `data-plane/src/xdp_gateway.bpf.c`, `data-plane/src/node_control.h`, `data-plane/tests/test_parse.c` + `tests/pkt_build.h` (modify).
**Depends on:** None
**Reuses:** `node_control.h` module shape; `svc_stat_map` (proves HASH-by-`dp_id` read in XDP); `l3_rewrite_nexthop` eth-`memcpy` idiom (bounds-check kept, fib call dropped); `record_drop` choke point; `test_meta_map`/`pkt_build.h`.
**Requirement:** NHR-01, NHR-02, NHR-03, NHR-04, NHR-05, NHR-06

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [x] `struct nexthop { u8 dst_mac[6]; u8 src_mac[6]; u8 resolved; u8 _pad; u64 last_resolved_ns; }` + `nexthop_map` (`HASH`, `BPF_F_NO_PREALLOC`, `max_entries=1024`, key `u32 dp_id`) in `nexthop.h`; `_Static_assert` on value size.
- [x] `nexthop_rewrite(ctx, meta)` returns `0`=rewritten (`memcpy` dst/src MAC after eth bounds-check), `<0`=must drop; hot path leaves IP dst/ports/**TTL**/checksum unchanged.
- [x] `DR_NEXTHOP_UNRESOLVED = 16` appended (`DROP_REASON_COUNT` 16→17, cap 32, `_Static_assert` holds) + `drop_reason_name[16]="nexthop_unresolved"`.
- [x] `redirect_out()` = resolve-first, fail-closed: unresolved/missing → `record_drop(meta, DR_NEXTHOP_UNRESOLVED)` **before** `svc_stat_clean`; on success set `PKT_VERDICT_REDIRECT` + `svc_stat_clean` + `bpf_redirect_map`.
- [x] `l3_rewrite_nexthop` (fib) + its forward-decl **deleted**; `node_control.h` `redirect_out_bypass` no longer rewrites (verbatim; `(void)ctx`).
- [x] dp-unit cases: (a) seeded resolved entry → SYN → `XDP_REDIRECT`, dst=dst_mac, src=src_mac, IP/port/**TTL** intact; (b) no/`resolved=0` entry → `XDP_DROP` + `counter_map[16]`==1 + `svc_stat` drop bucket; (c) recovery after seeding a resolved entry; (d) bypass-on undeclared IPv4 → verbatim redirect (unchanged L2). Seed the map directly with `bpf_map_update_elem` in the harness.
- [x] Program loads **native + verifier-clean**; if `NO_PREALLOC HASH` visibility disappoints on the runner kernel, fall back to `ARRAY`-indexed-by-`dp_id` (same value struct/hot path) and note it.
- [x] Gate check passes: `make test`
- [x] Test count: `≥ B_dp + 4` (net after deleting any committed fib-rewrite cases; state exact live; no silent deletions)

**Tests:** dp-unit
**Gate:** quick
**Commit:** `feat(data-plane): map-based next-hop L2 rewrite, fail-closed on unresolved`

---

### DT2: Loader pin + dpstat next-hop writer/reader

**What:** Pin `nexthop_map`; add `dpstat` ARP-probe writer, manual writer, evictor, and dump + a `"nexthop"` block in `snapshot --json`.
**Where:** `data-plane/loader/loader.c`, `data-plane/tools/dpstat.c` (modify).
**Depends on:** DT1
**Reuses:** loader `set_*_pin_paths`/`pin_map`/`unpin_map`; `dpstat` `open_pinned_map` + `cmd_set_bypass` skeleton + `cmd_snapshot`; existing `tx_devmap[0]` (OUT ifindex); `AF_PACKET`/`SOCK_RAW`/`ETH_P_ARP` + `ioctl(SIOCGIFHWADDR/SIOCGIFADDR)`.
**Requirement:** NHR-09, NHR-12, NHR-13, NHR-15, NHR-16, NHR-17

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `NEXTHOP_MAP_PIN_PATH PIN_DIR "/nexthop_map"`; pinned in the runtime-map set + unpinned on cleanup; **no seed** (empty = fail-closed).
- [ ] `dpstat resolve-nexthop <dp_id> <ipv4>`: read OUT ifindex from `tx_devmap[0]` → `if_indextoname` → src_mac/spa via `ioctl`; send `ARPOP_REQUEST`, recv `ARPOP_REPLY` (`spa==target`) with bounded `retries×timeout`; on reply `bpf_map_update_elem(nexthop_map, dp_id, {dst=reply.sha, src=src_mac, resolved=1, last_resolved_ns=now}, BPF_ANY)` → exit 0; on exhaustion mark `resolved=0` → exit non-zero.
- [ ] `dpstat evict-nexthop <dp_id>` (`bpf_map_delete_elem`); `dpstat set-nexthop <dp_id> <dst_mac> [<src_mac>]` (no-ARP manual writer, for ops + the DT3 smoke); `dpstat nexthop` dump.
- [ ] `snapshot --json` gains `"nexthop":[{dp_id,dst_mac,src_mac,resolved,age_s}...]` + node `nexthop_unresolved` count; a missing pinned map stays an offline/error condition (not a partial snapshot).
- [ ] Gate check passes: `make bpf skel loader apply dpstat` (builds clean; `make apply` golden self-test still green)
- [ ] Test count: `B_dp` unchanged (real map write + ARP verified by the DT3 privileged smoke, per the DP build-gated-tooling pattern)

**Tests:** none (build gate; covered by DT3)
**Gate:** build
**Commit:** `feat(data-plane): dpstat resolve/evict/set-nexthop + loader pin + snapshot`

---

### DT3: Live veth next-hop smoke

**What:** Privileged two-veth smoke: static rewrite via `set-nexthop`, real ARP via `resolve-nexthop` against a veth peer, and the unresolved fail-closed drop.
**Where:** `data-plane/tests/smoke_nexthop.sh` (new) + `Makefile` `smoke` target (modify).
**Depends on:** DT1, DT2
**Reuses:** `make smoke` harness (`smoke_bypass.sh` structure), native-XDP loader, `tx_devmap[0]`.
**Requirement:** NHR-01, NHR-02, NHR-03, NHR-09, NHR-12

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] Load gateway on veth, seed one enabled service; `dpstat set-nexthop <dp_id> <peer_mac>` → crafted IPv4 frame egresses with dst/src MAC rewritten, **TTL + IPv4 checksum unchanged**.
- [ ] `dpstat resolve-nexthop <dp_id> <peer_ip>` against a veth peer answering ARP → entry becomes `resolved=1` with the peer's MAC; the next frame rewrites to it.
- [ ] With no entry (or `evict-nexthop`) → the matched-service frame is **dropped**, `snapshot --json` shows `nexthop_unresolved` incremented.
- [ ] Ctrl-C detaches cleanly and removes pins.
- [ ] Gate check passes: `make test && sudo make smoke` (all smokes green, serial)
- [ ] Test count: `make test` unchanged; `smoke_nexthop.sh` passes

**Tests:** dp-integration
**Gate:** full
**Commit:** `test(data-plane): live veth next-hop rewrite + ARP-resolve smoke`

---

### CT1: Single-IPv4-host service destination

**What:** Reject a non-`/32` (multi-address CIDR) service destination on create/update; ship a read-only report of existing non-host services.
**Where:** `control-plane/app/services/` (service create/update), `control-plane/app/cli.py` or an admin read path (report), `control-plane/tests/integration/` (modify/add).
**Depends on:** None
**Reuses:** existing `core/cidr` helpers, service-create validation + 422 field-error pattern, overlap/`cidr_in_tenant_allocation` guards (unchanged).
**Requirement:** NHR-07, NHR-08

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] Create/update rejects a destination whose network is not a single host (`ip_network(dest,strict=False).num_addresses != 1`) with a 422 field error; a bare host and an explicit `/32` are accepted.
- [ ] Existing overlap + tenant-allocation guards still fire (unchanged behavior).
- [ ] A read-only report lists existing non-`/32` services (no auto-conversion — D-035-M).
- [ ] Integration cases: `/24` → 422; host/`/32` → 201; overlap + allocation guards; report lists a seeded non-host row.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `≥ B_cp + 3` (state exact live)

**Tests:** integration
**Gate:** full
**Commit:** `feat(control-plane): constrain protected-service destination to a single IPv4 host`

---

### CT2: NextHopResolver worker lane

**What:** A background lane that ARP-resolves enabled services (immediate queue + 30-min reconcile) via a `dpstat`-exec writer, with a fake for tests.
**Where:** `control-plane/app/worker/nexthop_resolver.py` (new), `control-plane/tests/integration/test_nexthop_resolver.py` (new).
**Depends on:** None
**Reuses:** `node_control_reconciler.py` (`NodeControlReconciler` + `DpstatBypassWriter` + `FakeBypassWriter`) as the template verbatim; `committed_db` fixture; `session_scope`.
**Requirement:** NHR-10, NHR-11, NHR-13, NHR-14, NHR-20, NHR-21

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `NextHopWriter` Protocol (`resolve(dp_id, ip)->bool`, `evict(dp_id)->bool`); `DpstatNextHopWriter` (subprocess-exec `resolve-nexthop`/`evict-nexthop` with timeout, mirrors `DpstatBypassWriter`); `FakeNextHopWriter` recording calls.
- [ ] `NextHopResolver.request_resolve(dp_id, ip)` / `request_evict(dp_id)` enqueue onto an `asyncio.Queue` drained promptly; `resolve_once()` reads enabled services (`dp_id`, `cidr_or_ip`) from DB, resolves each, and **evicts** entries not in the enabled set; `run_loop(stop)` drains the queue + `resolve_once()` every `interval` (immediate on start).
- [ ] Integration cases (`committed_db` + `FakeNextHopWriter`): reconcile resolves enabled services; a failed resolve marks/leaves unresolved (fail-closed, no keep-last); disable/delete → evict; `request_resolve` drains promptly; interval loop ticks + stops on the event.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `≥ B_cp + 6` (state exact live)

**Tests:** integration
**Gate:** full
**Commit:** `feat(worker): NextHopResolver ARP-resolution lane`

---

### CT3: Worker wiring + immediate-resolve post-apply hook

**What:** Spawn the lane in the worker runtime with settings, and request an immediate resolve/evict after a service apply.
**Where:** `control-plane/app/worker/worker.py`, `control-plane/app/worker/processor.py` (or `handlers.py`), `control-plane/app/core/config.py` (modify); `tests/integration/test_worker_runtime.py`, `test_worker_processor.py` (modify).
**Depends on:** CT2
**Reuses:** `worker.py` `*Lane` Protocol + `asyncio.create_task(lane.run_loop(stop))` wiring; `worker_node_control_*` settings shape; the `SERVICE_UPDATE` apply path.
**Requirement:** NHR-10, NHR-14

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `NextHopLane` Protocol + `asyncio.create_task(nexthop.run_loop(stop))` spawned + drained on shutdown; constructed with `DpstatNextHopWriter` + `worker_nexthop_resolve_interval_seconds` (1800), `worker_nexthop_probe_timeout_seconds`, `worker_nexthop_probe_retries`, reuse `dpstat` binary path.
- [ ] After a successful `SERVICE_UPDATE` apply → `nexthop.request_resolve(dp_id, ip)`; on a disable/delete apply → `request_evict(dp_id)`.
- [ ] Integration cases: runtime spawns/stops the lane; a processed apply fires `request_resolve` (assert via a fake); a disable fires `request_evict`.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `≥ B_cp + 3` (state exact live)

**Tests:** integration
**Gate:** full
**Commit:** `feat(worker): spawn next-hop lane + immediate resolve on apply`

---

### CT4: /node/health unresolved-services count (P2)

**What:** Surface the count of enabled services currently unresolved (blackhole indicator) from the snapshot `"nexthop"` block.
**Where:** `control-plane/app/worker/telemetry_reader.py` (or the node-health assembly) + `app/api/routers/node.py` (modify); `tests/integration/` (modify).
**Depends on:** CT2
**Reuses:** `TelemetryReader.snapshot()` + `FakeTelemetryReader` (feed a snapshot with a `"nexthop"` block); the M6 `/node/health` router.
**Requirement:** NHR-18, NHR-19

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `/node/health` reports `unresolved_services` (enabled services with `resolved=0`/absent) from the snapshot `"nexthop"` block; a resolved↔unresolved transition is log/telemetry-observable.
- [ ] Integration cases (`FakeTelemetryReader` with a nexthop block): mixed resolved/unresolved → correct count; all-resolved → 0.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `≥ B_cp + 2` (state exact live)

**Tests:** integration
**Gate:** full
**Commit:** `feat(control-plane): report unresolved next-hop services in node health`

---

### CT5: Manual resolve endpoint + resolve metrics (P3)

**What:** Admin "resolve now" trigger + per-service resolve success/failure/age surface.
**Where:** `control-plane/app/api/routers/node.py` (or `services`), `app/worker/nexthop_resolver.py` (metrics) (modify); `tests/integration/` (modify).
**Depends on:** CT2, CT3
**Reuses:** `require_admin`; `NextHopResolver.request_resolve`; the snapshot `last_resolved`/`resolved` fields.
**Requirement:** NHR-20, NHR-21

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] Admin `POST` "resolve now" for a service triggers an out-of-band resolve (reuses `request_resolve`); non-admin denied.
- [ ] Per-service resolve success/failure counts + last-resolved age readable.
- [ ] Integration cases: admin trigger enqueues a resolve; RBAC denial; metrics reflect fake outcomes.
- [ ] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [ ] Test count: `≥ B_cp + 2` (state exact live)

**Tests:** integration
**Gate:** full
**Commit:** `feat(control-plane): manual next-hop resolve trigger + resolve metrics`

---

### CT6: Docs [P]

**What:** Document the map-based next-hop mechanism, superseding the fib-lookup note; single-IP service constraint; new dpstat subcommands.
**Where:** `data-plane/README.md`, `PRD.md` (§8.2 note), `control-plane` service docs, spec/design status lines (modify).
**Depends on:** DT1, DT2, CT1
**Reuses:** existing README/PRD forwarding sections (the AD-DP-01 "transparent bridge vs routed next-hop rewrite" section is rewritten to the map-based design).
**Requirement:** (docs for NHR-01..21)

**Tools:** MCP: NONE · Skill: `docs-writer`

**Done when:**
- [ ] `data-plane/README.md` next-hop section rewritten (map-based, fail-closed, `dpstat` subcommands, bypass=verbatim); PRD §8.2 note updated; the `bpf_fib_lookup` mechanism marked superseded.
- [ ] Single-IPv4-host service constraint documented.
- [ ] Gate check passes: docs build/lint if any (else prose review); no code gate.
- [ ] Test count: n/a (docs)

**Tests:** none
**Gate:** none (docs)
**Commit:** `docs: map-based next-hop L2 rewrite + single-IP service`

---

## Pre-Approval Validation

### Check 1 — Task Granularity

| Task | Scope | Status |
| ---- | ----- | ------ |
| DT1 | 1 mechanism (map + ABI + hot-path verdict) across tightly-coupled headers + its dp-unit tests | ✅ Cohesive-not-split — the fail-closed drop is untestable without the map + ABI + helper + rewire all present (compilation-dependency merge; mirrors M6 bypass T1) |
| DT2 | loader pin + dpstat subcommands (DP userspace wiring, one map) | ✅ Cohesive-not-split — mirrors M6 bypass T2 (loader pin + dpstat set-bypass/snapshot combined), build-gated |
| DT3 | 1 privileged smoke script | ✅ Granular |
| CT1 | 1 validation rule + report | ✅ Granular |
| CT2 | 1 worker lane module | ✅ Granular |
| CT3 | worker wiring + 1 post-apply hook | ✅ Cohesive (spawn + hook are one integration surface) |
| CT4 | 1 health field | ✅ Granular |
| CT5 | 1 endpoint + metrics | ✅ Granular |
| CT6 | docs | ✅ Granular |

### Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram shows | Status |
| ---- | ----------------- | ------------- | ------ |
| DT1 | None | (DP lane start) | ✅ Match |
| DT2 | DT1 | DT1 → DT2 | ✅ Match |
| DT3 | DT1, DT2 | DT2 → DT3 (DT1 transitively) | ✅ Match |
| CT1 | None | (CP lane start) | ✅ Match |
| CT2 | None | CP lane (parallel-independent of CT1 in code; serial only on shared test infra) | ✅ Match |
| CT3 | CT2 | CT2 → CT3 | ✅ Match |
| CT4 | CT2 | CT3 → CT4 (CP serial tail; code-dep on CT2) | ✅ Match |
| CT5 | CT2, CT3 | CT4 → CT5 (CP serial tail) | ✅ Match |
| CT6 | DT1, DT2, CT1 | `[P]` after subjects | ✅ Match |

*Note:* the CP chain is drawn serial (`CT1→CT2→CT3→CT4→CT5`) because integration tests share `compose.test.yml` (the bottleneck), even where code deps are looser (CT1/CT2 have no code dep on each other). No two `[P]` tasks depend on each other (only CT6 is `[P]`).

### Check 3 — Test Co-location Validation

| Task | Code layer modified | Matrix requires | Task says | Status |
| ---- | ------------------- | --------------- | --------- | ------ |
| DT1 | XDP verdict path (`data-plane/src/*`) | dp-unit | dp-unit | ✅ OK |
| DT2 | loader + dpstat (DP userspace tooling) | build-gated; DP pattern covers via privileged smoke | none (build) + DT3 smoke | ✅ OK (DP-established: loader/dpstat build-gated, covered by the privileged smoke — not deferral) |
| DT3 | privileged veth path | dp-integration | dp-integration | ✅ OK |
| CT1 | service create/update (API/service) | integration | integration | ✅ OK |
| CT2 | worker lane | integration | integration | ✅ OK |
| CT3 | `worker.py`/`processor.py` | integration | integration | ✅ OK |
| CT4 | `api/routers/node.py` + reader | integration | integration | ✅ OK |
| CT5 | `api/routers` + lane | integration | integration | ✅ OK |
| CT6 | docs | none | none | ✅ OK |

All three checks pass.

---

## Tooling plan (established default — confirm at approval)

- **Code tasks (DT1–DT3, CT1–CT5):** `coding-guidelines` skill · no MCPs.
- **Docs (CT6):** `docs-writer` skill.
- Consistent with every prior feature in this project.

## Requirement Coverage

All 21 `NHR-` requirements mapped: NHR-01/02→DT1,DT3 · 03→DT1 · 04→DT1 · 05→DT1 · 06→DT1 · 07/08→CT1 · 09→DT2,DT3 · 10→CT2,CT3 · 11→CT2 · 12→DT2,DT3 · 13→DT2,CT2 · 14→CT2,CT3 · 15→DT2 · 16→DT2 · 17→DT2 · 18/19→CT4 · 20→CT5,CT2 · 21→CT5,CT2.
