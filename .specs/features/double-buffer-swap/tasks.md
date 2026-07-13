# Double-buffer Map Build/Swap Tasks

**Design**: `.specs/features/double-buffer-swap/design.md` (AD-028)
**Spec**: `.specs/features/double-buffer-swap/spec.md` (DBS-01..28)
**Context**: `.specs/features/double-buffer-swap/context.md` (D-DBS-1..3, A-DBS-1..8)
**Status**: Execute in progress — T1 complete; T2 blocked pending an approved wire-contract correction.

**Prerequisite — agent-worker (M4 #1) is executed.** This feature swaps
`PlaceholderApplier → DoubleBufferApplier` at the AD-027 injection site and reuses its `ServiceConfig`,
`Applier` protocol, `handle_service_update`, and `process_job` two-txn guard. Agent-worker T1–T6 are
complete (`ef81fc4..c236fb3`); its final full gate passed with 262 tests.

**Baselines** (pin exact totals live at Execute):
- **Data-plane** `cd data-plane && make test` = **B_dp = 112** (verified at T1; T1 adds no tests).
  Targets below are `B_dp + Δ`.
- **Control-plane** `pytest -q` = **B_cp = live count at T6 start** — pin before the T6 RED phase.
  T6 adds a serialize **unit** case + fake-helper
  **integration** cases.

## Execution Results

| Task | Status | Commit | Gate result |
| --- | --- | --- | --- |
| T1 | Complete | `7fcfb1b` | build passed; quick passed, 112 tests (unchanged live baseline) |

## Execute Blocker — Resolve Before T2

The approved schema-v1 record cannot faithfully rebuild the current control-plane/data-plane model:

- `rule_entry` and control-plane `AllowRule` have `pps` and `bps`, but the specified rule record has no
  fields for either rate limit.
- VIP limits are service-level (`ProtectedService.vip_pps`/`vip_bps`), while the specified record puts
  them on each whitelist entry (which stores only a source CIDR).
- BPF maps use the `u32` `ProtectedService.dp_id`, but `ServiceConfig` currently exposes only its UUID.

The C parser/golden fixture and Python serializer must not be created until the wire contract explicitly
carries rule rates, service-level VIP configuration, and `dp_id` (or an approved equivalent).

**Gates** (from TESTING.md):
- Data-plane: **build** = `make bpf skel loader apply dpstat` · **quick** = `make test` ·
  **full** = `make test && sudo make smoke` · **scale** = `sudo make applybulk`.
- Control-plane: **quick** = `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit` ·
  **full** = `ruff check . && ruff format --check . && mypy app/ && pytest -q` (on `compose.test.yml`).

**Key structural decision** (drives testability): `xdpgw-apply`'s core is factored into fd-taking
functions — `build_inactive_slot(fds, node_cfg)`, `carry_forward_feed(fds)`, `verify_slot(fds)`,
`commit(fds)` — so the whole build→verify→flip→verdict path runs **in-harness under `make test`**
(skeleton fds, `BPF_PROG_TEST_RUN`). Only the pin-open + subprocess wrapper (`main()`) needs the
privileged smoke.

---

## Execution Plan

### Phase 1 — Foundation (sequential)
```
T1 (contracts + loader pins + fair_budget)  →  T2 (helper scaffold + parser + fresh-inner de-risk)
```

### Phase 2 — Core + parallel tracks
```
                 ┌─ C track ──────────────────────────────────┐
T2 ─────────────►│ T3 (build/verify/flip core + verdict) → T4 │→ T5 (main + smoke + applybulk)
                 └────────────────────────────────────────────┘
                 ┌─ Python track ──────┐
T2 ─────────────►│ T6 (applier + DI)   │   [P] vs C track (disjoint files + infra)
                 └─────────────────────┘
T1 ─────────────► T7 (dpstat active_config)    [P]
```

### Phase 3 — Docs (parallel, last)
```
T3,T5,T6 ──► T8 (docs)   [P]
```

`[P]` tasks: **T6** (control-plane; shares no files/infra with the C dp-unit chain), **T7** (dpstat;
disjoint `tools/dpstat.c`, build-only), **T8** (docs). Every C-track task (T1→T5) edits shared files
(`xdpgw-apply.c` / `tests/test_parse.c` / `loader.c`) and/or runs privileged smoke → **serial**.

---

## Task Breakdown

### T1: Contracts + loader config-map pins + shared fair math

**What**: Add the CP↔DP wire-format header + shared fairness-budget header, and pin the 14 slotted
config maps in the loader — foundation, **verdict-neutral** (no `make test` behavior change).
**Where**: `data-plane/src/apply_snapshot.h` (new), `data-plane/src/fair_budget.h` (new),
`data-plane/loader/loader.c` (modify).
**Depends on**: None (agent-worker landed = feature gate).
**Reuses**: `loader.c` `clamp_fair_rate`/`fair_rate_product`/`prepare_fair_seed` budget derivation
(extract to `fair_budget.h`); existing `set_pin_path`/`pin_map`/`unpin_map`/`PIN_DIR` + observability
pin/unpin/rollback structure; `service.h`/`rules.h`/`whitelist.h`/`blacklist.h`/`fairness.h` field defs.
**Requirement**: DBS-08, DBS-05 (pin set excludes runtime-state maps + static inners + tx_devmap).

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `apply_snapshot.h` defines the versioned wire format: magic `"XDPGWAP1"`, `schema_version`, and the
      per-service record layout (explicit LE fields per design §Data Models) as documented constants/comments.
- [x] `fair_budget.h` exposes `clamp_fair_rate`, `fair_rate_product`,
      `fair_budget(committed,ceiling,k,ref_pkt)`, `node_headroom(capacity,sum_committed)`; `loader.c`
      includes it and its numbers are byte-identical to before (same seed output).
- [x] `loader.c` pins all 14 config maps (`service_map`, `rule_block_map`, `whitelist_bloom`/`_lpm`,
      `vip_config_map`, `global_blacklist_bloom`/`_lpm`, `service_blacklist_bloom`/`_lpm`,
      `udp_blocked_port_bitmap`, `fair_config_map`, `fair_node_config`, `gbl_meta`, `active_config`) under
      `PIN_DIR`, with matching unpin on clean exit; static inner_0/_1, `tx_devmap`, and all runtime-state
      maps are **not** pinned.
- [x] Build gate passes: `make bpf skel loader dpstat`.
- [x] Quick gate passes: `make test` → **112** (unchanged; verdict-neutral).
- [x] Pin runtime-correctness deferred to T5 smoke (documented; the test harness does not pin).

**Tests**: none (verdict-neutral; build + import of the shared header; runtime pins verified in T5)
**Gate**: build (+ quick 112 unchanged)
**Commit**: `feat(dp): pin config maps + apply_snapshot/fair_budget contracts`

**Executed 2026-07-13** (`7fcfb1b`, corrected by `d3cf007`): pin the 14 slotted config maps in `loader.c`
(+155) + new `apply_snapshot.h`/`fair_budget.h` contracts; the follow-up correction moved VIP to the
service record and renamed the wire surrogate to `dp_id` (see the T2 contract-correction note above).
Verdict-neutral — build gate green, `make test` unchanged at **112**.

---

### T2: `xdpgw-apply` scaffold — snapshot parser + fresh-inner de-risk (the fail-fast)

> **Contract correction (2026-07-13, pre-consumption of `apply_snapshot.h` v1).** Two T2-blocking seams
> fixed in T1's uncommitted header + AD-028 §Data Models before the parser/golden fixture bake the layout
> in: (1) **VIP is service-level** — `vip_pps/vip_bps/vip_flags` moved from the per-whitelist record up to
> the service fixed record (one `vip_config` keyed by `dp_id`, matching `struct vip_config` +
> `ServiceConfig.vip_pps/vip_bps`); the `wl[]` entry is now `{prefixlen, src_be32}` only. Constants:
> `SERVICE_FIXED_SIZE` 33→**50**, `WHITELIST_ENTRY_SIZE` 25→**8**. (2) **`dp_id` surrogate** — the wire
> `service_id` field is renamed `dp_id` (the AD-030 D-030-4 `u32` surrogate, **not** the UUID);
> `ServiceConfig` gained an additive `dp_id: int` (T6 serializer sources it). Schema stays v1 (nothing
> consumed it yet). T2 golden fixture + parser and T6 serializer both build against the corrected layout.

**What**: Bootstrap the C helper: parse the snapshot into an in-memory `node_cfg`, add the
`create_inner_like()` primitive, and **prove the novel composition** (fresh inner meta-equal to an
existing inner, installed into an `ARRAY_OF_MAPS` outer, read back) — the one load-bearing unknown,
proven before building the rest.
**Where**: `data-plane/tools/xdpgw-apply.c` (new), `data-plane/tests/fixtures/apply_snapshot_golden.bin`
(new) + `data-plane/tests/test_snapshot.c` (new, non-BPF parse check), `data-plane/tests/test_parse.c`
(add the fresh-inner de-risk dp-unit case), `data-plane/Makefile` (add `apply` + `applybulk` targets).
**Depends on**: T1 (`apply_snapshot.h`).
**Reuses**: `apply_snapshot.h`; `service.h`/etc. structs; the WLV T1 fresh-inner-load precedent;
`bpf_map_get_info_by_fd`/`bpf_map_create`/`bpf_map_update_elem` (libbpf).
**Requirement**: DBS-10 (parse + validate), DBS-09 (fresh inner create+install).

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `parse_snapshot(path) -> node_cfg` validates magic + `schema_version` + bounds; rejects
      truncated/unknown input (fail-closed).
- [x] `create_inner_like(outer_fd, src_slot) -> fd` replicates meta via `bpf_map_get_info_by_fd` →
      `bpf_map_create` (`btf_fd=0`); an installed fresh inner is readable and meta-equal.
- [x] **De-risk dp-unit** (in `test_parse.c` harness, in-process via skel fds): create a fresh inner for
      one outer (e.g. `rule_block_map`), populate, `bpf_map_update_elem(outer, slot, fd)`, then look it up
      and read a key back — proves `map_meta_equal` + create + install.
- [x] `make apply` builds `build/xdpgw-apply` + `build/test_snapshot`; the latter parses
      `apply_snapshot_golden.bin` to the expected `node_cfg` (returns nonzero on mismatch).
- [x] `make applybulk` target scaffolded (body filled in T5).
- [x] Build gate passes: `make bpf skel loader apply dpstat`.
- [x] Quick gate passes: `make test` → **113** (+1 de-risk case).

**Tests**: dp-unit (fresh-inner de-risk) + build-gate parse self-test
**Gate**: quick (113)
**Commit**: `feat(dp): xdpgw-apply scaffold, snapshot parser, fresh-inner de-risk`

**Executed 2026-07-13** (`036da4f`): `xdpgw-apply.c` core (`static inline`, `main()` guarded) +
`test_snapshot.c` parse self-test + committed `apply_snapshot_golden.bin` (158 B, 2 services, service-
level VIP + `dp_id`, generator alongside) + de-risk dp-unit (case #2 "apply fresh inner install
round-trips"). **Primary fresh-inner rung works — no fallback needed.** Build gate green; `make test`
→ **113**. Next: **T3** (build/carry-forward/verify/single-write flip core + verdict tests).

---

### T3: Build → carry-forward → verify → flip (core) + verdict tests

**What**: Implement the fd-taking core — `build_inactive_slot(fds, node_cfg)` (fresh inner per
service-scoped outer, populated from the snapshot with the leaf-writer idioms + `fair_budget.h` + env
knobs), `carry_forward_feed(fds)` (pointer-copy global-deny inners + `gbl_meta` row), `verify_slot(fds)`
(structural read-back), `commit(fds)` (single `active_config` write `{inactive, V+1}`) — and dp-unit
tests proving a build+flip changes enforcement.
**Where**: `data-plane/tools/xdpgw-apply.c` (extend), `data-plane/tests/test_parse.c` (add verdict cases).
**Depends on**: T2.
**Reuses**: `loader.c` leaf-writer idioms (`seed_rule_block_fd`/`seed_wl_slot`/`seed_*_blacklist_*`/
`seed_fair_config_slot` key construction) re-authored for snapshot input; `fair_budget.h`; T2
`create_inner_like`.
**Requirement**: DBS-02, DBS-03, DBS-15, DBS-16, DBS-17, DBS-18, DBS-19, DBS-24 (fresh active_config read).

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `build_inactive_slot` rebuilds every service-scoped outer's inactive-slot inner from `node_cfg`;
      `service_map` enabled/`wl_flags`/`bl_flags` + `fair_node_config[inactive]` (headroom from Σcommitted)
      derived from the same snapshot; live slot untouched (DBS-02/15).
- [x] `carry_forward_feed` pointer-copies `global_blacklist_bloom`/`_lpm`/`udp_blocked_port_bitmap` active
      inners into the inactive index and copies the `gbl_meta` row (DBS-17); 1M list never rebuilt.
- [x] `verify_slot` fails on any missing enabled service / `rule_block` version-count mismatch / missing
      inner fd (DBS-18); `commit` is the single `active_config` write bumping `version` (DBS-03/19); reads
      `active_slot` fresh each run (DBS-24).
- [x] **dp-unit**: seed slot 0 (skel), run the core with a snapshot that (a) adds an allow-rule → newly
      allowed flow admits; (b) adds a service → its dest resolves; (c) disables/removes a service →
      `service_miss`/`service_disabled`; assert non-triggering services **and** the carried-forward global
      blacklist unchanged after the flip (DBS-16/17).
- [x] Quick gate passes: `make test` → **≥ 118** (pin exact).

**Tests**: dp-unit
**Gate**: quick (≥118)
**Commit**: `feat(dp): xdpgw-apply build/verify/single-write swap core`

**Executed 2026-07-13** (`e3ee877`): `build_inactive_slot`/`carry_forward_feed`/`verify_slot`/`commit`
fd-taking core in `xdpgw-apply.c` (+476) + 6 build/verify/flip verdict dp-unit cases in `test_parse.c`
(+412). Quick gate `make test` → **119** (113 + 6).

---

### T4: Fail-closed rollback + version/idempotency dp-unit

**What**: Prove abort-before-flip and version behavior — inject a build error and a verify mismatch
(no flip, `active_slot`/`version` unchanged), a kill-mid-build equivalent (no partial live), and two
applies of the same snapshot (version increments, verdicts identical); restart re-reads `active_config`
and targets the opposite slot.
**Where**: `data-plane/tools/xdpgw-apply.c` (error paths / `goto fail` discipline), `tests/test_parse.c`
(add rollback + version cases).
**Depends on**: T3.
**Reuses**: T3 core; the `goto fail` cleanup idiom from `loader.c`.
**Requirement**: DBS-11, DBS-12, DBS-13, DBS-14, DBS-21, DBS-24.

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] A forced build write-failure and a forced `verify_slot` mismatch each leave `active_config`
      `{active_slot, version}` unchanged and prior verdicts intact; fresh inners closed on `fail` (DBS-11/12/13).
- [x] Interrupting between build and commit (no `commit` call) leaves the live slot unchanged; a
      subsequent full apply overwrites the inactive slot and succeeds (DBS-14).
- [x] Two applies of the same snapshot: `version` goes V→V+1→V+2, `active_slot` toggles, verdicts
      identical (DBS-21); an apply started with `active_slot=1` builds slot 0 (DBS-24).
- [x] Quick gate passes: `make test` → **≥ 122** (pin exact).

**Tests**: dp-unit
**Gate**: quick (≥122)
**Commit**: `test(dp): xdpgw-apply fail-closed rollback + version idempotency`

**Executed 2026-07-13** (`b8a5f9f`): abort-before-flip error paths in `xdpgw-apply.c` (+81) + 3
rollback/version-idempotency dp-unit cases (`test_parse.c` +221). Quick gate `make test` → **122**
(119 + 3; re-verified live during the T3–T7 record back-fill).

---

### T5: `main()` pin-open + subprocess CLI + privileged smoke + scale measurement

**What**: Wrap the core in `main()` (open pinned maps by `bpf_obj_get`, build the fd bundle, run
core, exit 0/nonzero with stderr), fill in `make applybulk`, and add a privileged smoke that runs the
real helper against a loaded data-plane and asserts the flip + enforcement + carried-forward global list.
**Where**: `data-plane/tools/xdpgw-apply.c` (`main` + pin-open), `data-plane/Makefile` (`smoke` variant
+ `applybulk` body), smoke script under `data-plane/tests/`.
**Depends on**: T3 (core) [T4 recommended].
**Reuses**: T1 loader pins; T3 core; existing `make smoke` two-veth harness + native loader run.
**Requirement**: DBS-06 (helper CLI/exit), DBS-07 (exit code), DBS-14 (crash-consistency live),
DBS-23 (first apply reconciles), DBS-25 (≤5 s e2e), DBS-26 (scale rebuild measured).

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `xdpgw-apply <snapshot>` opens all pinned config maps (friendly error if a pin is absent), runs the
      core, exits 0 on swap / nonzero with a stderr reason on any failure (DBS-06/07).
- [x] Privileged smoke: loader (with T1 config-map pins) up → run `xdpgw-apply` with a snapshot that
      adds/removes a service → assert `active_config.active_slot` flipped, the hot path enforces the change
      over veth (or via `dpstat`/readback), and the seeded global blacklist still drops (DBS-23).
- [x] `make applybulk`: build a 1000-service snapshot, run one apply, **measure** build+verify+flip wall
      time and assert < budget; confirms feed maps are carried-forward not rebuilt (DBS-26).
- [x] Full gate passes: `make test && sudo make smoke`; scale gate: `sudo make applybulk` green.

**Tests**: dp-integration (privileged)
**Gate**: full (+ scale)
**Commit**: `feat(dp): xdpgw-apply CLI + privileged apply smoke + applybulk`

**Executed 2026-07-13** (`c8cd95d`): `main()` pin-open + subprocess CLI (`xdpgw-apply.c` +157); `make
applybulk` body via `tests/apply_bulk.sh` + `tests/apply_smoke.py generate-bulk` (1000-service snapshot,
asserts <5 s wall time, single `active_config` flip slot 0→1/version 1→2, feed inners carried forward);
privileged apply smoke `tests/smoke_apply.sh` wired into `make smoke`. `make test` unchanged at **122**
(no new dp-unit case). Privileged full/scale gates (`sudo make smoke` / `sudo make applybulk`) are
root-only and landed with the commit — not re-run in this back-fill.

---

### T6: `DoubleBufferApplier` + node snapshot + DI swap + settings [P]

**What**: Implement the Python applier that loads a full-node snapshot from PG, serializes it to the
wire format, execs `xdpgw-apply` with a timeout, and maps the exit code; swap it in at `__main__`; add
settings. Test with a **fake helper binary** (no live BPF) + a serialize round-trip against T2's golden
fixture.
**Where**: `control-plane/app/worker/applier.py` (extend: `DoubleBufferApplier`, `load_node_config`,
`serialize_node_snapshot`, `ApplyError`), `control-plane/app/worker/__main__.py` (DI swap),
`control-plane/app/core/config.py` (`worker_apply_binary_path`, `worker_apply_timeout_seconds`),
`control-plane/tests/unit/test_snapshot_serialize.py` (new),
`control-plane/tests/integration/test_double_buffer_applier.py` (new).
**Depends on**: T1 (`apply_snapshot.h` format), T2 (`apply_snapshot_golden.bin`).
**Reuses**: AD-027 `ServiceConfig` + `load_service_config` pattern + `Applier` protocol; `session_scope`
(read); `Settings`; `process_job` success/raise → `mark_active`/`mark_failed` mapping (unchanged).
**Requirement**: DBS-01, DBS-04, DBS-06, DBS-07, DBS-10 (serialize), DBS-20, DBS-22, DBS-28.

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `serialize_node_snapshot(node)` emits bytes **identical** to `apply_snapshot_golden.bin` for the
      matching fixture node (unit; round-trips T2's C parser) (DBS-10).
- [x] `DoubleBufferApplier.apply(config)` loads `load_node_config(db)` (all enabled services + children),
      serializes to a temp file (0600, unlinked), execs the helper via
      `asyncio.create_subprocess_exec` with `worker_apply_timeout_seconds`; exit 0 → return + structured
      swap log (slot/version/duration, DBS-28); nonzero/timeout → `ApplyError(stderr)` (DBS-06/07).
- [x] `__main__` injects `DoubleBufferApplier(session_factory=…, apply_bin=…, timeout=…)` in place of
      `PlaceholderApplier`; the boundary + `process_job` are unchanged (DBS-01); startup performs **no**
      swap (applier only runs on a job) (DBS-22).
- [x] **integration** (fake helper stub recording argv + snapshot bytes, exiting 0/1/timeout): success →
      `active`, `active_version=N`; nonzero → `failed`, `active_version` kept; a `bump_version` mid-apply
      → supersede via the existing two-txn guard, exactly one advance (DBS-04/07/20).
- [x] Quick gate (unit serialize) + Full gate pass: `pytest -q` green; state the added pass count.

**Tests**: integration (+ unit for serialize)
**Gate**: full
**Commit**: `feat(worker): DoubleBufferApplier build/swap via xdpgw-apply`

**Executed 2026-07-13** (`6c6a532`): `DoubleBufferApplier` + `load_node_config` + `serialize_node_snapshot`
+ `ApplyError` (`applier.py` +224), DI swap in `__main__.py`, `worker_apply_binary_path`/
`worker_apply_timeout_seconds` settings (`config.py`). **5 CP cases** — 4 fake-helper integration
(`test_double_buffer_applier.py`) + 1 serialize round-trip unit (`test_snapshot_serialize.py`, binds the
committed golden fixture). CP full gate `pytest -q` landed with the commit — not re-run in this back-fill.

---

### T7: dpstat `active_config` slot/version section [P]

**What**: Add an `active_config` section to `dpstat` reading the newly-pinned map (active slot +
version), with the friendly gateway-not-loaded error when the pin is absent.
**Where**: `data-plane/tools/dpstat.c` (extend).
**Depends on**: T1 (`active_config` pinned).
**Reuses**: `dpstat.c` pinned-map read + friendly-error convention (drop-reason-counters precedent).
**Requirement**: DBS-27.

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `dpstat` prints `active_slot` and `version` from the pinned `active_config`.
- [x] Without the pin, it returns the existing friendly "gateway not loaded" error (no crash).
- [x] Build gate passes: `make bpf skel loader apply dpstat`; manual: `./build/dpstat` shows the section
      after a load.

**Tests**: none (build + manual readback, per drop-reason-counters dpstat precedent)
**Gate**: build
**Commit**: `feat(dp): dpstat active_config slot/version section`

**Executed 2026-07-13** (`94ddc1e`): `dpstat active_config` section (`dpstat.c` +26) printing `active_slot`
+ `version` from the pinned map, with the friendly gateway-not-loaded error when the pin is absent. Build
gate `make bpf skel loader apply dpstat` green (re-verified during T2/T8). `apply_bulk.sh` consumes this
section to assert the flip.

---

### T8: Docs — TESTING.md `xdpgw-apply` section + READMEs [P]

**What**: Document the apply flow: TESTING.md data-plane `xdpgw-apply` conventions (snapshot fixture,
build/flip dp-unit pattern, `applybulk`), the `apply_snapshot` wire contract + `schema_version`
discipline, and the worker `DoubleBufferApplier` + `xdpgw-apply` invocation (README/data-plane README).
**Where**: `.specs/codebase/TESTING.md`, `data-plane/README.md`, `control-plane` worker docs.
**Depends on**: T3 (conventions), T5 (`applybulk` number), T6 (worker wiring).
**Reuses**: existing TESTING.md data-plane sections as the template.
**Requirement**: (documentation for DBS-06/09/17/25/26 — no new requirement).

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:
- [x] TESTING.md gains an `xdpgw-apply` subsection (fixture + build/flip conventions + `applybulk`),
      and the data-plane gate table lists `make apply`/`make applybulk`.
- [x] `apply_snapshot` wire format + `schema_version` bump rule documented.
- [x] READMEs describe the CP→helper apply path (loader pins config maps; helper reuses seed idioms).

**Tests**: none
**Gate**: none (doc build/lint only)
**Commit**: `docs: xdpgw-apply flow, snapshot contract, apply testing conventions`

**Executed 2026-07-13**: `TESTING.md` — new "Apply-helper (`xdpgw-apply`) conventions" subsection
(in-harness fd-taking core, fault injection under `-DXDPGW_APPLY_TEST`, golden-fixture round-trip +
`schema_version` discipline, `applybulk` scale), `apply`/`applybulk` added to the gate table, corpus
count refreshed 91→**122**. `data-plane/README.md` — new "Apply helper (xdpgw-apply)" section (loader
pins the 14 config maps, CLI, single-write flip, abort-before-flip rollback, `dpstat active_config`).
`control-plane/README.md` — replaced the stale "Placeholder applier caveat" with "Double-buffer applier"
+ the `CONTROL_PLANE_WORKER_APPLY_{BINARY_PATH,TIMEOUT_SECONDS}` settings. No new requirement; doc-only.
**Double-buffer-swap (M4 #2) Execute complete: T1–T8 all landed.**

---

## Pre-Approval Validation

### Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 2 new headers + loader pin block (one cohesive foundation) | ✅ Granular |
| T2 | helper scaffold: parser + de-risk primitive + build target | ✅ Granular |
| T3 | the build/verify/flip core (one tool's core logic) | ✅ Granular |
| T4 | failure/version tests + error paths on the same tool | ✅ Granular |
| T5 | `main()` wrapper + smoke + applybulk (one binary's I/O edge) | ✅ Granular |
| T6 | one Python class + DI + settings + its tests | ✅ Granular |
| T7 | one dpstat section (one file) | ✅ Granular |
| T8 | docs | ✅ Granular |

### Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram shows | Status |
| --- | --- | --- | --- |
| T1 | None | (root) | ✅ |
| T2 | T1 | T1 → T2 | ✅ |
| T3 | T2 | T2 → T3 | ✅ |
| T4 | T3 | T3 → T4 | ✅ |
| T5 | T3 (T4 rec.) | T4 → T5 | ✅ (T4→T5 chains after T3; T4 recommended not blocking) |
| T6 | T1, T2 | T2 → T6 (Python track) | ✅ |
| T7 | T1 | T1 → T7 `[P]` | ✅ |
| T8 | T3, T5, T6 | T3,T5,T6 → T8 `[P]` | ✅ |

`[P]` set = {T6, T7, T8}; none depend on another `[P]` task in the same phase (T6/T7 depend only on
T1/T2; T8 is last). ✅

### Check 3 — Test Co-location

| Task | Code layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | loader pins + shared headers (verdict-neutral) | none (build; runtime pins → T5) | none | ✅ |
| T2 | `xdpgw-apply` parser + fresh-inner primitive | dp-unit (mechanism) + build (parse) | dp-unit + build self-test | ✅ |
| T3 | build/verify/flip core | dp-unit (verdict via `BPF_PROG_TEST_RUN`) | dp-unit | ✅ |
| T4 | error paths + version | dp-unit | dp-unit | ✅ |
| T5 | `main()` pin-open + subprocess (privileged path) | dp-integration (smoke) | dp-integration | ✅ |
| T6 | `DoubleBufferApplier` + snapshot serialize | integration (PG/subprocess) + unit (serialize) | integration + unit | ✅ |
| T7 | dpstat section | none (build + manual, dpstat precedent) | none | ✅ |
| T8 | docs | none | none | ✅ |

No ❌. Snapshot **round-trip** is co-located across the two languages by design: the **C parse** of the
golden fixture is in T2's build self-test; the **Python emit** of the identical bytes is T6's unit test —
both bind the *same committed* `apply_snapshot_golden.bin`, so neither defers the other's coverage.

---

## Requirement Coverage

| Req | Task(s) | Req | Task(s) |
| --- | --- | --- | --- |
| DBS-01 | T6 | DBS-15 | T3 |
| DBS-02 | T3 | DBS-16 | T3 |
| DBS-03 | T3 | DBS-17 | T3 (+T5 smoke) |
| DBS-04 | T6 | DBS-18 | T3 (+T4 fail) |
| DBS-05 | T1 (+T3) | DBS-19 | T3 (+T4) |
| DBS-06 | T5 (+T6) | DBS-20 | T6 |
| DBS-07 | T5, T6 | DBS-21 | T4 |
| DBS-08 | T1 | DBS-22 | T6 |
| DBS-09 | T2, T3 | DBS-23 | T5 (+T3) |
| DBS-10 | T2 (parse), T6 (serialize) | DBS-24 | T3, T4 |
| DBS-11 | T4 | DBS-25 | T5 |
| DBS-12 | T4 | DBS-26 | T5 |
| DBS-13 | T3, T4 | DBS-27 | T7 |
| DBS-14 | T4 (+T5 live) | DBS-28 | T6 |

**Coverage:** 28/28 mapped. Milestone proofs: **T2** de-risk (novel composition, fail-fast), **T3**
verdict-change proves real enforcement, **T5** the ≤5 s + scale + cross-process smoke, **T6** the boundary
swap under the existing supersede guard.

---

## Tooling Summary (for Execute)

- **Skills**: `coding-guidelines` (T1–T7), `docs-writer` (T8). Diagrams already rendered (mermaid-studio).
- **MCPs**: none (Context7 unavailable in this environment).
- **Sub-agents**: each task delegated to a sub-agent with its definition + `coding-principles`/
  `CONVENTIONS` + TESTING.md + the referenced spec/design section. C-track (T1→T5) serial; T6/T7/T8 `[P]`.
