# Service Lookup & Transparent Redirect — Tasks

**Design:** `.specs/features/service-lookup-redirect/design.md`
**Spec:** `.specs/features/service-lookup-redirect/spec.md` (SLRD-01..26)
**Context:** `.specs/features/service-lookup-redirect/context.md` (D-SLRD-1..3, A-SLRD-1..8)
**Status:** **VERIFIED / EXECUTED (2026-07-08)** — T1–T7 complete and committed; final full gate passed
(`make test && sudo make smoke` → 29 dp-unit tests + delivered TTL/csum unchanged). Resume = next M2
feature.
**Execute tooling (chosen):** Skill `coding-guidelines` on the C/XDP + shell tasks (T1–T6); MCPs: none
configured. Docs (T7): none. **Execution mode: inline** (like packet-parse).

> **Gates** (run from `data-plane/`, established by packet-parse): **build** = `make bpf skel loader` ·
> **quick** = `make test` (`BPF_PROG_TEST_RUN`, `-DPKT_TEST_HOOKS`) · **full** = `make test` + `make smoke`
> (privileged live-veth). Test types (`.specs/codebase/TESTING.md`): **dp-unit** = synthetic-packet
> `BPF_PROG_TEST_RUN` (parallel-safe as infra, but tasks sharing `xdp_gateway.bpf.c`/`test_parse.c`
> serialize); **dp-integration** = live veth (privileged, **not** parallel-safe) — this feature populates
> it for the first time (T6/T7).
>
> **Execution result:** the pre-execute baseline was **21** dp-unit tests; the final suite passes **29**
> dp-unit tests. The clean-IPv4 and ARP terminals changed from `XDP_PASS` to redirect, with their tests
> migrated in place (no silent deletions).
>
> **De-risk note:** the `ARRAY_OF_MAPS`-of-`LPM_TRIE` representation (design Open Q#1) is **proven at load
> in T2** — if the toolchain/verifier rejects it, T2 switches to the documented fallback (two named
> top-level LPM maps + slot branch, same external contract) **before** any hot-path code is written.

---

## Execution Plan

### Phase 1: Contracts & maps (Sequential foundation)

```
T1 (contract headers) ──→ T2 (config maps + load de-risk)
```

### Phase 2: Hot path + loader (T5 parallel with T3)

```
T2 ──┬──→ T3  (service seam: pin + LPM + verdicts + redirect + tests + migrate IPv4 tests)
     └──→ T5 [P]  (loader: OUT arg + populate tx_devmap + seed — separate file loader/loader.c)
```

### Phase 3: ARP seam (Sequential — shares hot-path files)

```
T3 ──→ T4  (ARP redirect seam + migrate ARP test)
```

### Phase 4: Live forwarding (Sequential — needs full program + loader)

```
(T3, T4, T5) ──→ T6  (live-veth IN↔OUT smoke: real XDP_REDIRECT + TTL/csum — dp-integration)
```

### Phase 5: Docs

```
T6 ──→ T7  (TESTING.md: populate dp-integration / redirect smoke conventions)
```

**Parallel summary:** `T5` is the only `[P]` task — it edits `loader/loader.c` (+ Makefile `run`), touched
by no other task, and has no automated tests, so it runs alongside `T3`. Everything else serializes:
`T3→T4` share `xdp_gateway.bpf.c`/`test_parse.c`; `T6` (dp-integration) is not parallel-safe.

---

## Task Breakdown

### T1: Contract headers — `service.h` + `pkt_meta`/`drop_reason` extensions

**Status:** Complete — verified 2026-07-08 (`make bpf skel`)
**What:** Add the shared config-contract header and extend the two existing contract headers, keeping the
program building and every existing field/name intact.
**Where:** `data-plane/src/service.h` (new), `data-plane/src/pkt_meta.h` (modify),
`data-plane/src/drop_reason.h` (modify), `data-plane/src/xdp_gateway.bpf.c` (add `#include "service.h"`),
`data-plane/Makefile` (add `service.h` to bpf-obj prerequisites)
**Depends on:** None
**Reuses:** `design.md` → Components (`service.h` struct defs); packet-parse's plain-header style; existing
`enum drop_reason`/`counter_map`
**Requirement:** SLRD-05 (enum), SLRD-15/16 (structs), SLRD-18 (zero-init/padding)

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `service.h` defines `SERVICE_SLOTS 2`, `struct service_key {u32 prefixlen; __be32 addr;}`,
      `struct service_val {u32 service_id; u8 enabled; u8 _pad[3];}`, `struct active_config {u32 active_slot; u32 version;}`,
      and `enum pkt_verdict {PKT_VERDICT_NONE=0, PKT_VERDICT_REDIRECT=1}` (plain header, `<linux/types.h>`).
- [ ] `drop_reason.h` **appends** `DR_SERVICE_MISS`, `DR_SERVICE_DISABLED` after `DR_MAP_ERROR` (indices
      0–4 unchanged); `DROP_REASON_CAP=32` unchanged.
- [ ] `pkt_meta.h` adds `__u32 service_id`, `__u8 active_slot`, `__u8 verdict`, keeps all existing fields/names
      and 4-byte alignment (per design struct); `= {}` still zero-inits.
- [ ] `xdp_gateway.bpf.c` includes `service.h`; `Makefile` bpf/test-bpf objects list `service.h` as a prereq.
- [ ] Gate check passes: `make bpf skel` compiles the object + regenerates the skeleton with no errors.
**Tests:** none · **Gate:** build

**Verify:** `cd data-plane && make bpf skel && ls build/xdp_gateway.skel.h` → exit 0, skeleton present.
**Commit:** `feat(dataplane): service.h config contract + pkt_meta/drop_reason extensions`

---

### T2: Config maps + load de-risk (`service_map` map-in-map, `active_config`, `tx_devmap`)

**Status:** Complete — verified 2026-07-08 (`make test` → 22 passed)
**What:** Declare the slot-aware config maps and the devmap, and prove they **build, generate a skeleton,
and load into the kernel** — the go/no-go for the map-in-map+LPM representation (fallback here if not).
**Where:** `data-plane/src/xdp_gateway.bpf.c` (map declarations only — no hot-path logic yet),
`data-plane/tests/test_parse.c` (resolve new map fds in `test_env`; add one "config maps load" assertion)
**Depends on:** T1
**Reuses:** `service.h` structs; existing inline-map style (`test_meta_map`); verified map-in-map rule (any
inner except `PROG_ARRAY`; LPM inner needs `BPF_F_NO_PREALLOC`)
**Requirement:** SLRD-15, SLRD-16 (SLRD-07 devmap decl)

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `xdp_gateway.bpf.c` declares `service_inner_0`/`service_inner_1` (`LPM_TRIE`, key `service_key`,
      value `service_val`, `BPF_F_NO_PREALLOC`), `service_map` (`ARRAY_OF_MAPS`, `max_entries=SERVICE_SLOTS`,
      `.values={[0]=&service_inner_0,[1]=&service_inner_1}`), `active_config` (`ARRAY`, 1 entry,
      `active_config` value), `tx_devmap` (`DEVMAP`, 1 entry). Not behind `PKT_TEST_HOOKS`.
- [ ] Program still returns at both seams (unchanged behavior) — maps declared, not yet used.
- [ ] `test_env` resolves fds for `service_inner_0/1`, `active_config`, `tx_devmap` (and keeps counter/meta);
      one dp-unit test asserts all fds are valid ⇒ **the object loaded** (verifier accepted the maps).
- [ ] **If load fails** (map-in-map/LPM unsupported): switch to the fallback (two named top-level LPM maps
      `service_map_0/1` + `active_config` + `tx_devmap`), record the switch in `design.md` Tech Decisions,
      and re-run — the external contract (slot selected by `active_slot`) is unchanged.
- [ ] Gate check passes: `make test`. Test count: **22** (21 baseline + 1 maps-load) — no existing test
      changes behavior yet.
**Tests:** dp-unit · **Gate:** quick

**Verify:** `cd data-plane && make test` → `22 passed`; the maps-load test fails loudly if the
representation doesn't load (proving/denying feasibility here, not later).
**Commit:** `feat(dataplane): slot-aware service_map (map-in-map LPM) + active_config + tx_devmap`

---

### T3: Service-lookup seam — slot pin + LPM + verdicts + redirect (+ migrate clean-IPv4 tests)

**Status:** Complete — verified 2026-07-08 (`make test` → 29 passed)
**What:** Replace the service-lookup seam: pin `active_slot`, LPM-lookup `dst_ip` in the pinned slot, emit
`service_miss`/`service_disabled`/`map_error` or `bpf_redirect_map`; add seed helpers + the service dp-unit
tests; migrate the clean-IPv4 tests whose `XDP_PASS` terminal is gone.
**Where:** `data-plane/src/xdp_gateway.bpf.c` (service seam hot path), `data-plane/tests/test_parse.c`
(seed helpers `seed_service`/`set_active`/`reset_config`; new service tests; migrate IPv4 verdict tests)
**Depends on:** T2
**Reuses:** `pkt_meta` (`dst_ip`), `record_drop`, `write_test_meta`/`test_meta_map`; the maps from T2;
verified `bpf_redirect_map(flags=XDP_DROP)` fail-closed semantics
**Requirement:** SLRD-01, SLRD-02, SLRD-03, SLRD-04, SLRD-05 (record), SLRD-06, SLRD-07, SLRD-08, SLRD-09,
SLRD-12, SLRD-13, SLRD-14, SLRD-17 (test seed), SLRD-23

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] Hot path (after `parse_l4 == PARSE_OK`): read `active_config[0].active_slot` once → `meta.active_slot`;
      `bpf_map_lookup_elem(&service_map,&slot)` → inner (null → `record_drop(DR_MAP_ERROR)`); LPM lookup
      `{prefixlen=32, addr=meta.dst_ip}` → null → `DR_SERVICE_MISS`; `enabled==0` → `DR_SERVICE_DISABLED`;
      else set `service_id`/`verdict=REDIRECT`, `write_test_meta`, `return bpf_redirect_map(&tx_devmap,0,XDP_DROP)`.
- [ ] Program mutates **no** packet bytes (TTL/checksum untouched — SLRD-08/09); no per-source-IP state.
- [ ] `test_parse.c` seed helpers added; `reset_config` clears inners + `active_config` between tests.
- [ ] New dp-unit tests: `service_miss` drop; `service_disabled` drop; enabled → `meta.verdict==REDIRECT` +
      `service_id` + `active_slot==0` + drop counters 0; `/24` CIDR matches a host inside it; **slot-pin flip**
      (slot0 enabled `active=0` → REDIRECT decision, then slot1 disabled `active=1` on same frame →
      `DR_SERVICE_DISABLED`); empty-config → `DR_SERVICE_MISS`.
- [ ] **Migrated** clean-IPv4 tests (TCP/UDP/ICMP ports, GRE/ESP zero-ports, single-VLAN, QinQ): seed an
      enabled service for their `dst_ip`, assert `meta.verdict==REDIRECT` + the same parse-field values
      (no `retval==XDP_PASS`, no `retval==XDP_REDIRECT` — not observable under test-run per design).
- [ ] Gate check passes: `make test`. Test count: **~28** (22 + 6 new service tests; migrated tests updated
      in place) — finalize exact count at Execute; must be ≥ 22, no deletions.
**Tests:** dp-unit · **Gate:** quick

**Verify:** `cd data-plane && make test` → all pass; enabled-service frame asserts
`test_meta_map[0].{verdict==1, service_id, active_slot}`; disabled frame asserts `counter[DR_SERVICE_DISABLED]==1`.
**Commit:** `feat(dataplane): service lookup + service_miss/disabled + XDP_REDIRECT decision (slot-pinned)`

---

### T4: ARP redirect seam (+ migrate ARP test)

**Status:** Complete — verified 2026-07-08 (`make test` → 29 passed)
**What:** Switch the ARP seam from `XDP_PASS` to the same `tx_devmap` redirect (verbatim), and migrate the
ARP test to assert the redirect decision with no drop counter.
**Where:** `data-plane/src/xdp_gateway.bpf.c` (ARP branch), `data-plane/tests/test_parse.c` (migrate ARP
test; extend `expect_all_drop_counters_zero` upper bound to `DR_SERVICE_DISABLED`)
**Depends on:** T3
**Reuses:** the single redirect helper + `write_test_meta` from T3 (SLRD-12/22 — no second forwarding path)
**Requirement:** SLRD-19, SLRD-20, SLRD-21, SLRD-22

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] ARP branch sets `meta.verdict=REDIRECT`, `write_test_meta`, `return bpf_redirect_map(&tx_devmap,0,XDP_DROP)`
      (D-SLRD-3); the `/* SEAM: redirect ARP … */` comment is resolved.
- [ ] `expect_all_drop_counters_zero` covers `DR_IPV6_UNSUPPORTED..DR_SERVICE_DISABLED`.
- [ ] ARP test migrated: asserts `meta.verdict==REDIRECT` and **all** drop counters 0 (ARP never
      mis-counted/dropped, SLRD-21).
- [ ] Gate check passes: `make test`. Test count: **~28** (ARP test migrated in place; ≥ T3 count).
**Tests:** dp-unit · **Gate:** quick

**Verify:** `cd data-plane && make test` → all pass; ARP frame asserts `test_meta_map[0].verdict==1` and
every drop counter `==0`.
**Commit:** `feat(dataplane): ARP transparent-bridge redirect IN→OUT (closes packet-parse seam)`

---

### T5: Loader — `OUT` arg + populate `tx_devmap` + seed [P]

**Status:** Complete — verified 2026-07-08 (`make loader`)
**What:** Extend the loader to take an `OUT` interface, populate `tx_devmap` (fail-loud), and seed
`active_config` + one demo service so `make run` forwards.
**Where:** `data-plane/loader/loader.c`, `data-plane/Makefile` (`run` gains `OUT`), `data-plane/README.md`
(usage + seeding)
**Depends on:** T2
**Reuses:** existing open/load/attach/query/signal-detach flow; `skel->maps.{tx_devmap,active_config,service_inner_0}`;
`if_nametoindex`
**Requirement:** SLRD-11, SLRD-10 (load-time fail-loud), SLRD-17 (loader seed)

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] Loader accepts `OUT` (`argv[2]` or `OUT_IFACE`), resolves its ifindex; usage string updated to
      `<IN> <OUT>`.
- [ ] Populates `tx_devmap[0]=out_ifindex` before/after attach; on update failure (OUT lacks native XDP-TX /
      bad index) prints a clear error and `exit(1)` (**fail-loud**, SLRD-10/11) — no silent continue.
- [ ] Seeds `active_config[0]={active_slot=0,version=1}` and, if `SERVICE_DEST` env is set, one
      `service_val{enabled=1}` into `service_inner_0` (A-SLRD-5); absent `SERVICE_DEST`, maps stay empty
      (safe `service_miss` default).
- [ ] Gate check passes: `make loader` builds `build/xdp_gateway_loader`.
- [ ] Manual smoke recorded in README: `sudo ./build/xdp_gateway_loader <inveth> <outveth>` populates OUT or
      errors clearly (no automated loader test in v1 — matrix "none").
**Tests:** none · **Gate:** build

**Verify:** `cd data-plane && make loader` exit 0; `sudo ./build/xdp_gateway_loader veth_in veth_out` prints
attach mode + populated OUT, or a clean fail-loud error on a non-XDP-TX OUT.
**Commit:** `feat(dataplane): loader OUT interface + tx_devmap populate (fail-loud) + demo seed`

---

### T6: Live-veth redirect smoke — real `XDP_REDIRECT` + TTL/checksum (dp-integration)

**Status:** Complete — verified 2026-07-08 (`make test && sudo make smoke` → 29 passed + delivered TTL/csum unchanged)
**What:** A privileged two-veth `IN↔OUT` test that loads the program, seeds a service, sends a frame into
`IN`, captures it on `OUT`, and asserts real forwarding with **TTL + IPv4 checksum byte-identical**.
**Where:** `data-plane/tests/smoke_redirect.sh` (new), `data-plane/Makefile` (`smoke` target)
**Depends on:** T3, T4, T5
**Reuses:** the loader (T5) for attach + populate; the full redirect hot path (T3/T4); design smoke shape
**Requirement:** SLRD-08 (proven), SLRD-09 (proven), SLRD-24, SLRD-25

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `make smoke` (privileged): creates two veth pairs / netns as `IN`/`OUT`, runs the loader, seeds one
      enabled service, injects a crafted IPv4 frame on `IN`, captures on `OUT`.
- [ ] Asserts the received frame is delivered and its **TTL and IPv4 header checksum equal** the sent frame
      (header-preserving); exits non-zero on no-delivery or mismatch.
- [ ] Cleans up veths/netns/attachment on exit (pass or fail).
- [ ] Documented as requiring `CAP_NET_ADMIN`/root; **not** part of `make test` (not parallel-safe).
- [ ] Gate check passes (on a BPF+veth-capable runner): `make test && make smoke` (full gate).
**Tests:** dp-integration · **Gate:** full

**Verify:** `cd data-plane && sudo make smoke` → prints delivered + `TTL/csum unchanged`, exit 0; tamper the
program to decrement TTL → smoke fails (guards the assertion).
**Commit:** `test(dataplane): live-veth IN→OUT redirect smoke with TTL/checksum preservation`

---

### T7: TESTING.md — populate dp-integration / redirect smoke conventions

**Status:** Complete — verified 2026-07-08 (`.specs/codebase/TESTING.md` documents `make smoke` dp-integration)
**What:** Fill the previously-"Future" `dp-integration` row with the concrete `make smoke` two-veth
redirect/TTL-csum convention and add `make smoke` to the gate table.
**Where:** `.specs/codebase/TESTING.md` (Data-plane section)
**Depends on:** T6
**Reuses:** `design.md` test strategy; the existing data-plane section (A-PKT-2)
**Requirement:** SLRD-26 (A-SLRD-8)

**Tools:** MCP: NONE · Skill: NONE

**Done when:**
- [ ] `dp-integration` row documents: `make smoke`, two-veth `IN↔OUT`, real `XDP_REDIRECT` + TTL/checksum
      assertion, `CAP_NET_ADMIN`/root, not parallel-safe.
- [ ] Gate table `full` row updated to `make test` + `make smoke`; the loader-smoke note is preserved.
- [ ] Existing dp-unit content + control-plane section unchanged.
**Tests:** none · **Gate:** none

**Verify:** `.specs/codebase/TESTING.md` shows a populated dp-integration row + `make smoke` in the full
gate; dp-unit/control-plane sections intact.
**Commit:** `docs(testing): populate data-plane dp-integration (redirect veth smoke) conventions`

---

## Pre-Approval Checks

### Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 3 contract headers (cohesive: the shared contract) + include/Makefile wiring | ✅ Granular |
| T2 | Map declarations (one concern: the config-map contract) + load assertion | ✅ Granular |
| T3 | One hot-path seam (service decision) + its co-located tests + forced migration | ✅ Cohesive (matches packet-parse T6 pattern) |
| T4 | One hot-path seam (ARP) + its test | ✅ Granular |
| T5 | One file (loader) + its Makefile target | ✅ Granular |
| T6 | One test script + `smoke` target | ✅ Granular |
| T7 | One doc section | ✅ Granular |

### Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (root) | ✅ |
| T2 | T1 | T1→T2 | ✅ |
| T3 | T2 | T2→T3 | ✅ |
| T5 | T2 | T2→T5 `[P]` | ✅ |
| T4 | T3 | T3→T4 | ✅ |
| T6 | T3, T4, T5 | T3→T6, T4→T6, T5→T6 | ✅ |
| T7 | T6 | T6→T7 | ✅ |

`[P]` check: T5 (only `[P]`) shares no file with its phase-mate T3 (`loader/loader.c`+Makefile`run` vs
`xdp_gateway.bpf.c`+`test_parse.c`) and has no tests → parallel-safe. ✅

### Check 3 — Test Co-location Validation

| Task | Layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | contract headers (no runtime behavior) | build (structs verified by compile) | none / build | ✅ (precedent: packet-parse T1) |
| T2 | XDP program maps (+ load) | dp-unit | dp-unit / quick | ✅ |
| T3 | XDP program hot path | dp-unit | dp-unit / quick | ✅ |
| T4 | XDP program hot path | dp-unit | dp-unit / quick | ✅ |
| T5 | loader | none (build + manual smoke, matrix "none") | none / build | ✅ (precedent: packet-parse T2) |
| T6 | live redirect path | dp-integration | dp-integration / full | ✅ (T6 *is* the dp-integration test) |
| T7 | docs | none | none / none | ✅ |

No violations — every task that changes the XDP program carries its dp-unit tests + quick gate in the same
task; the live path is proven by T6's dp-integration; no test deferral.

---

## Requirement Traceability (26 total → all mapped)

| Req | Description | Task |
| --- | --- | --- |
| SLRD-01 | LPM lookup on dst IPv4 | T2 (map), T3 (lookup) |
| SLRD-02 | `service_miss` on no match | T3 |
| SLRD-03 | `service_disabled` (drop-all) | T3 |
| SLRD-04 | enabled → proceed + carry `service_id` | T3 |
| SLRD-05 | two new reasons recorded | T1 (enum), T3 (record) |
| SLRD-06 | map-error fail-closed | T3 |
| SLRD-07 | `XDP_REDIRECT` via `tx_devmap` | T2 (decl), T3 (call), T5 (populate) |
| SLRD-08 | TTL/checksum unchanged | T3 (no mutation), T6 (proven) |
| SLRD-09 | verbatim frame / tags preserved | T3, T6 |
| SLRD-10 | empty `tx_devmap` fail-closed + observable | T3 (runtime), T5 (load-time fail-loud) |
| SLRD-11 | loader `OUT` + populate + report | T5 |
| SLRD-12 | single redirect helper | T3, T4 (reuse) |
| SLRD-13 | pin `active_slot` once | T3 |
| SLRD-14 | flip takes effect per-packet | T3 (slot-pin test) |
| SLRD-15 | slot-aware `service_map` | T2 |
| SLRD-16 | `active_config` (`active_slot`+`version`) | T1 (struct), T2 (map) |
| SLRD-17 | userspace seed path | T3 (test seed), T5 (loader seed) |
| SLRD-18 | zero-init / padding | T1 |
| SLRD-19 | ARP bridge policy (redirect) | T4 |
| SLRD-20 | ARP verbatim | T4 |
| SLRD-21 | ARP not mis-counted/dropped | T4 |
| SLRD-22 | ARP reuses redirect helper | T4 |
| SLRD-23 | unit decision tests | T3, T4 |
| SLRD-24 | live forward TTL/csum | T6 |
| SLRD-25 | gated separate target | T6 |
| SLRD-26 | TESTING.md dp-integration update | T7 |

**Coverage:** 26 total, 26 mapped to tasks, 0 unmapped ✅
</content>
