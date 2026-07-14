# Bypass & Maintenance Mode Tasks

**Design:** `.specs/features/bypass-maintenance/design.md` (AD-032)
**Spec:** `.specs/features/bypass-maintenance/spec.md` (BYP-01..33)
**Status:** Executed (2026-07-14) — all task commits landed and task-scoped gates pass.

**Baselines (pin live at Execute):** dp `make test` **B_dp** (≥91 + telemetry `svc_stat` additions);
cp `pytest -q` **B_cp** (≥262 + feed + telemetry); fe Vitest **B_fe** (telemetry shell count).

**Tools (all code tasks):** Skill `coding-guidelines`; docs Skill `docs-writer` (T11). **No MCPs** — the
design is fully grounded in-repo (Context7 recorded unavailable; nothing needs external lookup).

**Not hard-gated on M4 #2** (AD-032 D-032-1): the bypass indicator lives in a dedicated `node_control`
map, so the whole feature builds on the executed **M1 + M2 + M4 #1** base (maintenance gate works today
with `PlaceholderApplier`). Soft coordination: M4 #2 applier (the flip maintenance defers), M5 telemetry
`/node/health`+`TelemetryReader` (T7/T6 extend, don't duplicate), M5 SPA shell (T10 P2 banner).

---

## Execution Plan

Three toolchain tracks run concurrently — **DP** (`make test`, `data-plane/`) ∥ **CP** (`pytest`,
`control-plane/`) ∥ **FE** (`npm`, `control-plane/frontend/`). Within CP, only **unit** tasks are `[P]`
(integration shares `compose.test.yml`).

```
Phase 1 (track leads, cross-track parallel):
  DP:  T1  (node_control.h + hot-path branch, dp-unit)
  CP:  T4  (NodeControl model + migration)      ┐ integration chain
       T6  (TelemetryReader bypass ext) [P] unit ┘ (T6 parallel)

Phase 2:
  DP:  T1 ─→ T2  (loader pins/seed + dpstat set-bypass + snapshot bypass, build)
  CP:  T4 ─→ T5  (services/node_control.py + audit)

Phase 3:
  DP:  T1,T2 ─→ T3  (live veth bypass smoke, dp-integration, privileged)
  CP:  T5 ─→ T8  (maintenance dispatch gate, processor)
       T5,T6 ─→ T7  (node router + schemas + main.py)

Phase 4:
  CP:  T4,T5,T8 ─→ T9  (NodeControlReconciler lane + writer + settings + worker spawn)
  FE:  T7 ─→ T10 (SPA bypass/maintenance banner, P2) [P]

Phase 5:
  T11 (docs: TESTING.md + OLA runbook) [P]
```

**Serial within CP** (shared `compose.test.yml`): T4 → T5 → T8 → T7 → T9. Only **T6** (unit) and **T10**
(FE, separate toolchain) and **T11** (docs) are `[P]`. DP T1→T2→T3 serialize on shared
`xdp_gateway.bpf.c`/`loader.c`/`dpstat.c` + the privileged smoke.

---

## Task Breakdown

### T1: Data-plane bypass core — `node_control.h` + hot-path short-circuit

**What:** New `src/node_control.h` (the `node_control` + `bypass_counter` maps, `node_control_bypass()`,
`bypass_count()`, `redirect_out_bypass()`) and the one-line post-parse bypass branch in `xdp_gateway.bpf.c`.
**Where:** `data-plane/src/node_control.h` (new), `data-plane/src/xdp_gateway.bpf.c` (edit),
`data-plane/tests/` (dp-unit cases).
**Depends on:** None
**Reuses:** `svc_stat.h` per-CPU counter idiom (`PERCPU_ARRAY`, `__sync_fetch_and_add`); `redirect_out`/
`tx_devmap` (`xdp_gateway.bpf.c:129`); `pkt_meta.frame_len`.
**Requirement:** BYP-09, BYP-10, BYP-11, BYP-12, BYP-13, BYP-14, BYP-15, BYP-16 (partial — DP side)

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] `node_control` `ARRAY[1]` (`{__u32 bypass; __u32 _reserved}`) + `bypass_counter` `PERCPU_ARRAY[1]`
      (`{__u64 pkts; __u64 bytes}`) defined; `node_control_bypass()` reads the single aligned `__u32`.
- [x] `redirect_out_bypass()` sets `verdict=REDIRECT`, `write_test_meta`, `bypass_count(meta)`,
      `bpf_redirect_map(&tx_devmap,0,XDP_DROP)` — does **not** call `svc_stat_clean`.
- [x] `ETH_P_IP` branch: after `parse_l4` OK, `if (node_control_bypass()) return redirect_out_bypass(&meta);`
      before `service_lookup_redirect`; ARP path unchanged.
- [x] dp-unit: harness writes `node_control.bypass` via `bpf_map_update_elem` (no new `PKT_TEST_HOOKS`),
      asserting: bypass=1 + would-be-`service_miss` IPv4 → `verdict=REDIRECT` + `bypass_counter` pkts/bytes
      advance + `svc_stat` for that dp_id **unchanged**; bypass=1 does **not** rescue IPv6/malformed/fragment
      (still their drop reasons); bypass=0 → normal `service_lookup_redirect` verdict.
- [x] Gate passes: `make test`
- [x] Test count: **B_dp + new cases** pass (no silent deletions)

**Tests:** dp-unit
**Gate:** quick (`make test`)
**Commit:** `feat(bypass): xdp soft-bypass short-circuit + node-global counter`

---

### T2: Data-plane userspace wiring — loader pins/seed + dpstat `set-bypass`/snapshot

**What:** Pin `node_control` + `bypass_counter` in the loader group and seed `node_control.bypass=0`; add
`dpstat set-bypass 0|1` (writes the pinned map) and a `node_control`/`bypass` block to `dpstat snapshot --json`.
**Where:** `data-plane/loader/loader.c` (edit), `data-plane/tools/dpstat.c` (edit).
**Depends on:** T1
**Reuses:** loader pin/unpin group (`loader.c:189-213`) + `seed_active_config` (`loader.c:490`); dpstat
`open_pinned_map`/`snapshot --json`.
**Requirement:** BYP-05 (assert channel), BYP-17 (surface counter)

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] Loader pins both maps (mirrors the pin/unpin/rollback of `active_config`); `seed_node_control` writes
      `bypass=0` on load (fresh node = enforcing).
- [x] `dpstat set-bypass 0|1` opens the pinned `node_control` and `bpf_map_update_elem(&0,{bypass})`.
- [x] `dpstat snapshot --json` emits `"node_control":{"bypass":0|1}` and `"bypass":{"pkts":N,"bytes":N}`
      (sum the `PERCPU_ARRAY`) — matching the JSON contract T6 parses.
- [x] Gate passes: `make bpf skel loader apply dpstat`
- [x] `./build/dpstat set-bypass 1` without pinned maps returns the existing friendly not-loaded error.

**Tests:** none (build gate; live behavior verified in T3 dp-integration smoke — the DP-established
loader/tooling → build, smoke → separate privileged task pattern)
**Gate:** build
**Commit:** `feat(bypass): loader pins/seed + dpstat set-bypass and snapshot block`

---

### T3: Data-plane live bypass smoke

**What:** Privileged two-veth smoke: load, `dpstat set-bypass 1`, send an IPv4 frame to an **undeclared**
dst, assert `IN→OUT` redirect (would normally `service_miss`-drop); `set-bypass 0`, assert drop resumes;
assert `bypass_counter` advanced via `dpstat snapshot`.
**Where:** `data-plane/tests/` (smoke variant), `data-plane/Makefile` (if a smoke target needs wiring).
**Depends on:** T1, T2
**Reuses:** existing `make smoke` two-veth redirect harness (TTL/csum smoke precedent).
**Requirement:** BYP-09, BYP-15, BYP-17 (end-to-end)

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] Smoke: bypass on → undeclared-dst IPv4 delivered `IN→OUT` (verbatim, TTL/csum unchanged); bypass off
      → same frame dropped; `bypass_counter` reflects the forwarded frames.
- [x] Gate passes: `make test && sudo make smoke`
- [x] Test count: **B_dp** dp-unit still pass; smoke green.

**Tests:** dp-integration
**Gate:** full (`make test && sudo make smoke`)
**Commit:** `test(bypass): live veth soft-bypass redirect smoke`

---

### T4: `NodeControl` singleton model + migration

**What:** `NodeControl` singleton model (`CheckConstraint id=1`) with desired-state fields + Alembic
migration `..._00NN_node_control`.
**Where:** `control-plane/app/db/models.py` (edit), `control-plane/migrations/versions/..._node_control.py`
(new), `control-plane/tests/integration/test_node_control_model.py` (new).
**Depends on:** None
**Reuses:** `TimestampMixin`, `SmallInteger`/`CheckConstraint` idioms, FK `ondelete="SET NULL"` to `users`.
**Requirement:** BYP-04, BYP-05, BYP-08, BYP-18, BYP-23, BYP-33 (persistence substrate)

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] `NodeControl`: `id SmallInteger PK CHECK(id=1)`, `bypass_enabled`, `maintenance_enabled`,
      `bypass_reason (≤512)`, `bypass_activated_at`, `maintenance_activated_at`, `bypass_actor_user_id`,
      `maintenance_actor_user_id` (FK users SET NULL), timestamps.
- [x] Migration up/down reversible; `down_revision` pinned to the then-current head at Execute.
- [x] Integration test: singleton constraint (second row with `id≠1` or duplicate rejected), defaults
      (both flags false), FK SET NULL on actor delete.
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: **B_cp + new** pass; `alembic upgrade head` + `downgrade` clean on the test DB.

**Tests:** integration
**Gate:** full
**Commit:** `feat(bypass): node_control singleton desired-state model + migration`

---

### T5: `services/node_control.py` — toggles + audit

**What:** `get_node_control` (get-or-create), `set_bypass`/`set_maintenance` (idempotent, activated_at,
dangerous-action audit), `maintenance_active`.
**Where:** `control-plane/app/services/node_control.py` (new),
`control-plane/tests/integration/test_node_control_service.py` (new).
**Depends on:** T4
**Reuses:** `record_event` + `AuditEvent` (`services/audit.py`); `session_scope` (`db/session.py`).
**Requirement:** BYP-01, BYP-02, BYP-06, BYP-07, BYP-08, BYP-18, BYP-23, BYP-31

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] `set_bypass(db, actor, enabled, reason, ip)` / `set_maintenance(...)` update the singleton, set/clear
      `*_activated_at`, write `record_event(action="node.bypass.enabled|disabled" / "node.maintenance.*")`.
- [x] Idempotent: toggling to the current state is a no-op (no row change, **no second audit**) — BYP-07.
- [x] `maintenance_active(db) -> bool` for the worker gate.
- [x] Integration test: enable/disable each writes exactly one audit event with the right action/outcome;
      idempotent re-toggle writes none; `activated_at` set on enable, cleared on disable; audit rows are
      queryable (BYP-31).
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: **B_cp + new** pass.

**Tests:** integration
**Gate:** full
**Commit:** `feat(bypass): node-control toggle service with dangerous-action audit`

---

### T6: `TelemetryReader` bypass extension [P]

**What:** Extend `TelemetrySnapshot` (+`NodeCounters`) with `bypass_active` / `bypass_pkts` / `bypass_bytes`
parsed from the dpstat `node_control`/`bypass` JSON block; keep `FakeTelemetryReader` in sync.
**Where:** `control-plane/app/worker/telemetry_reader.py` (edit),
`control-plane/tests/unit/test_telemetry_reader.py` (edit).
**Depends on:** None (parses the AD-032-fixed JSON contract; coordinates keys with T2, tests use a fixture)
**Reuses:** existing `TelemetrySnapshot.from_dict`/`to_dict` + `_mapping`/`_integer` parsers.
**Requirement:** BYP-25, BYP-26 (effective-state read)

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] `from_dict` parses `node_control.bypass` (→ `bypass_active`) + `bypass.{pkts,bytes}`; `to_dict`
      round-trips; missing block tolerated (defaults) for backward compat.
- [x] Unit test: round-trip a snapshot with/without the bypass block; asserts `bypass_active` + counters.
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`
- [x] Test count: **new unit cases** pass (no silent deletions).

**Tests:** unit
**Gate:** quick
**Commit:** `feat(bypass): parse node bypass state in the telemetry reader`

---

### T7: `/node` router — bypass/maintenance toggles + health

**What:** `api/routers/node.py` (`POST /node/bypass`, `POST /node/maintenance`, `GET /node/health`) +
`api/schemas/node.py`; register in `main.py`. `GET /node/health` merges desired (`NodeControl`) ⊕ effective
(`TelemetryReader.snapshot()`) + `activated_at`/`active_seconds`.
**Where:** `control-plane/app/api/routers/node.py` (new; or extend the telemetry node router if present),
`control-plane/app/api/schemas/node.py` (new), `control-plane/app/main.py` (edit),
`control-plane/tests/integration/test_node_router.py` (new).
**Depends on:** T5, T6
**Reuses:** `require_admin`/`get_current_user` (`core/deps.py`); router registration in `main.py::create_app`.
**Requirement:** BYP-01, BYP-03, BYP-18, BYP-19, BYP-24, BYP-25, BYP-26, BYP-27, BYP-33

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] `POST /node/bypass {enabled, reason?}` / `POST /node/maintenance {enabled}` → `set_*`, admin-only;
      non-admin → 403; `reason>512` → 422.
- [x] `GET /node/health` returns `bypass`/`maintenance` blocks each with `desired`, `effective`,
      `activated_at`, `active_seconds`, plus XDP mode / slot / version / bypass counter from the reader;
      `desired≠effective` visible when the reader reports offline (BYP-26).
- [x] Independence (BYP-24): both blocks reported independently.
- [x] Integration test (AsyncClient): admin toggle+read happy path; 403 non-admin; 422 long reason;
      desired-vs-effective drift via an injected `FakeTelemetryReader`.
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: **B_cp + new** pass.

**Tests:** integration
**Gate:** full
**Commit:** `feat(bypass): /node bypass, maintenance, and health API`

---

### T8: Maintenance apply-dispatch gate

**What:** `_maintenance_active(db)` check before `mark_applying` in `process_job`/`reconcile_once` — while
maintenance is on, leave `SERVICE_UPDATE` jobs `queued` (no build, no flip).
**Where:** `control-plane/app/worker/processor.py` (edit),
`control-plane/tests/integration/test_worker_processor.py` (edit).
**Depends on:** T5
**Reuses:** executed `process_job`/`reconcile_once` two-transaction flow; M1 `mark_*` guards; version-guarded
supersession.
**Requirement:** BYP-20, BYP-21, BYP-22

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] With maintenance on, a popped/reconciled `SERVICE_UPDATE` job is **not** dispatched — stays `queued`
      (no `mark_applying`); `apply_status` reflects `queued`.
- [x] With maintenance off, the same backlog drains and applies (latest per service, version-guarded).
- [x] Integration test (`committed_db`, injected applier): maintenance on → job holds `queued`, mutation
      still enqueues; maintenance off → drains to `active`; superseded older job no-ops.
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: **B_cp + new** pass.

**Tests:** integration
**Gate:** full
**Commit:** `feat(bypass): hold config apply dispatch during maintenance`

---

### T9: `NodeControlReconciler` lane + writer + settings + worker spawn

**What:** `NodeControlReconciler` (fast-tick lane: assert bypass on drift via `BypassWriter`, kick a
reconcile on the maintenance-clear edge, re-assert on startup) + `DpstatBypassWriter`/`FakeBypassWriter` +
`worker_node_control_*` settings + spawn/cancel in `Worker.run`.
**Where:** `control-plane/app/worker/node_control_reconciler.py` (new), `control-plane/app/core/config.py`
(edit), `control-plane/app/worker/worker.py` (edit),
`control-plane/tests/integration/test_node_control_reconciler.py` (new).
**Depends on:** T4, T5, T8
**Reuses:** `FeedCoordinator` background-lane lifecycle (`worker.py:46-51,225-238`); `TelemetryReader`
subprocess-exec pattern (for `DpstatBypassWriter`); `Settings` `worker_*` + `worker_telemetry_binary_path`/
`_ifindex` (dpstat path).
**Requirement:** BYP-04, BYP-05, BYP-06, BYP-14, BYP-22

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] `reconcile_once`: bypass desired≠asserted → `writer.set(bypass)`; not-loaded/error leaves effective
      unknown (drift visible); maintenance-clear edge → `on_maintenance_cleared` kicks a reconcile.
- [x] `run_loop(stop)` ticks every `worker_node_control_interval_seconds` (default 1.0), catch-log-continue;
      spawned as a standalone task in `Worker.run` (independent of the BRPOP/`process_job` loop → jumps the
      backlog), cancelled/awaited in `finally`.
- [x] Restart re-asserts bypass from the persisted `NodeControl` row (BYP-05).
- [x] `worker_node_control_enabled=True`, `worker_node_control_interval_seconds=1.0` in `Settings`.
- [x] Integration test (`committed_db`, `FakeBypassWriter`): bypass on → writer called with 1; row off →
      writer called with 0; restart re-assert; maintenance-clear → kick fires; independence from the apply
      loop (a blocked applier does not delay a bypass assert).
- [x] Gate passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`
- [x] Test count: **B_cp + new** pass.

**Tests:** integration
**Gate:** full
**Commit:** `feat(bypass): worker node-control reconcile lane and dpstat writer`

---

### T10: SPA bypass/maintenance banner (P2) [P]

**What:** A banner component in the telemetry SPA shell that polls `/node/health` and renders a persistent
critical "BYPASS ACTIVE" banner + a "MAINTENANCE" indicator on every route.
**Where:** `control-plane/frontend/src/` (banner component + API hook + Vitest tests beside source).
**Depends on:** T7 (API contract); **gated on the M5 telemetry SPA shell executed**.
**Reuses:** telemetry SPA shell + TanStack Query `refetchInterval:2000` + the typed API client.
**Requirement:** BYP-28, BYP-29, BYP-30

**Tools:** Skill `coding-guidelines`. No MCP.

**Done when:**
- [x] Banner shows when `bypass.effective` (critical style) and a "MAINTENANCE" indicator when
      `maintenance.effective`, on every route; reflects state within the 2 s poll.
- [x] Vitest: banner renders/clears from mocked `/node/health` states via the typed client.
- [x] Gate passes: `cd control-plane/frontend && npm run lint && npm run typecheck && npm run test -- --run && npm run build`
- [x] Test count: **B_fe + new** pass.

**Tests:** fe
**Gate:** fe
**Commit:** `feat(bypass): dashboard BYPASS ACTIVE / maintenance banner`

---

### T11: Docs — TESTING.md conventions + OLA runbook [P]

**What:** Document the bypass dp-unit seam + `node_control`/`set-bypass`/snapshot-bypass conventions in
TESTING.md; write the OLA runbook (when/how to engage bypass & maintenance, chargeback/accounting
implications, exit procedure) and a README note.
**Where:** `.specs/codebase/TESTING.md` (edit), `data-plane/README.md` / `control-plane` docs / an OLA
runbook doc.
**Depends on:** T1–T9 landed (documents real behavior).
**Reuses:** existing TESTING.md data-plane + worker sections.
**Requirement:** BYP-32 (OLA runbook)

**Tools:** Skill `docs-writer`. No MCP.

**Done when:**
- [x] TESTING.md: bypass dp-unit seam (harness writes `node_control`), `set-bypass`/snapshot-bypass, the
      node-control reconcile lane + maintenance-gate test conventions.
- [x] OLA runbook: engage/exit bypass + maintenance, bypass-counted-separately/chargeback note, restart
      survival.
- [x] Gate: docs render; no code gate.

**Tests:** none
**Gate:** none (docs)
**Commit:** `docs(bypass): OLA runbook + TESTING.md bypass/maintenance conventions`

---

## Pre-Approval Validation

### Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 header + 1 hot-path branch + dp-unit | ✅ Granular |
| T2 | loader pins/seed + dpstat writer/snapshot (one DP-userspace bypass surface) | ⚠️ Cohesive-not-split (all build-gated, one `node_control`/counter contract; DP pattern = tooling→build) |
| T3 | 1 privileged smoke | ✅ Granular |
| T4 | 1 model + 1 migration | ✅ Granular |
| T5 | 1 service module (toggles + audit) | ✅ Granular |
| T6 | 1 reader dataclass extension | ✅ Granular |
| T7 | 1 router (3 endpoints, one resource) + schemas | ⚠️ Cohesive (one `/node` resource; endpoints share the model/reader) |
| T8 | 1 processor gate | ✅ Granular |
| T9 | 1 lane + writer + settings + spawn (one worker node-control unit) | ⚠️ Cohesive-not-split (lane is inseparable from its writer/settings/spawn) |
| T10 | 1 banner component | ✅ Granular |
| T11 | docs | ✅ Granular |

### Check 2 — Diagram ↔ `Depends on` Cross-Check

| Task | `Depends on` (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (Phase 1 lead) | ✅ |
| T2 | T1 | T1→T2 | ✅ |
| T3 | T1, T2 | T1,T2→T3 | ✅ |
| T4 | None | (Phase 1 lead) | ✅ |
| T5 | T4 | T4→T5 | ✅ |
| T6 | None | (Phase 1, [P]) | ✅ |
| T7 | T5, T6 | T5,T6→T7 | ✅ |
| T8 | T5 | T5→T8 | ✅ |
| T9 | T4, T5, T8 | T4,T5,T8→T9 | ✅ |
| T10 | T7 | T7→T10 | ✅ |
| T11 | T1–T9 | (Phase 5) | ✅ |

No `[P]` task depends on another `[P]` task in its phase (T6 ⊥ T1/T4; T10 ⊥ T9 across toolchains;
T11 last). ✅

### Check 3 — Test Co-location

| Task | Code layer | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | XDP program / hot path | dp-unit | dp-unit | ✅ |
| T2 | loader + dpstat tooling | build (dp-integration via T3 smoke — DP-established split) | none (build) | ✅ per DP convention |
| T3 | two-veth redirect | dp-integration | dp-integration | ✅ |
| T4 | models + migration | integration | integration | ✅ |
| T5 | audit/service | integration | integration | ✅ |
| T6 | telemetry reader (pure parse) | unit | unit | ✅ |
| T7 | API router | integration | integration | ✅ |
| T8 | worker processor | integration | integration | ✅ |
| T9 | worker runtime/lane | integration | integration | ✅ |
| T10 | frontend | fe-unit | fe | ✅ |
| T11 | docs | none | none | ✅ |

T2 is the only non-obvious row: loader/dpstat C tooling cannot be unit-tested (needs a loaded program +
pinned maps = privileged), so the repo's established DP pattern build-gates the tooling and covers it
end-to-end in the **privileged `dp-integration` smoke (T3)** — a first-class task, not a deferral (every
prior DP feature structured "loader/tooling → build, smoke → separate full task" identically).

---

## Requirement Coverage

All 33 mapped: **P1** BYP-01..27 (T1–T9), **P2** BYP-28..30 (T10), **P3** BYP-31 (T5 audit), BYP-32 (T11),
BYP-33 (T7). See the per-task **Requirement** fields; full req→component mapping in `design.md`
§Requirement Coverage.
