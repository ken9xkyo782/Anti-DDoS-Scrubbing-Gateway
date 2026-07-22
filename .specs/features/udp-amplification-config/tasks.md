# UDP Amplification Config & DDoS Protection Tab — Tasks

**Design**: `.specs/features/udp-amplification-config/design.md` (AD-036)
**Spec**: `.specs/features/udp-amplification-config/spec.md` (AMP-01..21)
**Status**: Draft — awaiting approval → Execute

**Baselines (pinned live at Execute):**
- `B_cp` = `pytest -q` head (control-plane; current head ≥ 507).
- `B_dp` = `make test` head (data-plane; TESTING.md = 130 dp-unit).
- `B_fe` = Vitest head total (frontend; current head ≥ 213).

Each task states its added-test floor and keeps the running total monotonic (no silent deletions).

**Flags resolved (from design F1–F7):** F1 `/ddos` + `/admin/ddos`; F2 drift-only reconcile
(loader-reload gap documented, periodic reassert deferred); F3 P2 read-back = Phase 5 (optional);
F4 overlap-with-hardcoded = accept + UI note; F5 single `GET /ddos/amplification`; F6 migration
number/`down_revision` pinned live; F7 lane wired in `worker.py`.

---

## Execution Plan

**3 concurrent tracks** (different toolchains/infra) + docs + optional P2:
- **DP** (`data-plane/`, C): DT1 → DT2 — own `make` toolchain (build + privileged veth).
- **CP** (`control-plane/`, Python): CT1 → CT2 → {CT3, CT4 → CT5} — **all integration**, serial on the
  single `compose.test.yml`.
- **FE** (`control-plane/frontend/`, TS): FT1 → FT2 — independent `fe` gate.

The DP↔CP contract is fixed here (`dpstat set-blocked-ports <p...>` argv + exit code + writes both
slots), so CP-worker tests use `FakeBlockedPortsWriter` and do **not** hard-gate on the DP track.

```
Phase 0 (track leads, concurrent):   CT1        DT1 [P]      FT1 [P]
                                      │            │            │
Phase 1:                             CT2        DT2          FT2 [P]
                                    ┌─┴─┐
Phase 2 (serial, shared compose):  CT3  CT4
                                         │
Phase 3:                                CT5
                                         │
Phase 4:                         DOC1 [P]  (needs DT1, CT5, FT2)

Phase 5 (P2, optional):          PT1  →  PT2 [P]
```

Dependency edges: CT1→CT2; CT2→CT3; CT2→CT4; CT4→CT5; DT1→DT2; FT1→FT2;
DOC1←{DT1,CT5,FT2}; PT1←DT1; PT2←{FT2,PT1}.

---

## Task Breakdown

### CT1: `BlockedUdpPort` model + migration

**What**: node-global desired-state table for dynamically blocked UDP source ports.
**Where**: `control-plane/app/db/models.py` (+ `app/db/migrations/versions/<pinned>_blocked_udp_port.py`).
**Depends on**: None
**Reuses**: `Base`, `utc_now`, `BlacklistEntry` column idioms; migration `_0010_node_control` (FK SET
NULL + CheckConstraint template).
**Requirement**: AMP-01

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `BlockedUdpPort(Base)`: `port` PK `Integer` + `CheckConstraint("port >= 0 AND port <= 65535",
      name="ck_blocked_udp_port_range")`; `note` `String(256)` nullable; `created_by` FK `users.id`
      `ondelete="SET NULL"` nullable; `created_at` `DateTime(timezone=True)` default `utc_now`;
      `__tablename__ = "blocked_udp_port"`.
- [x] Migration `create_table` (mirrors `_0010`), `down_revision` pinned to the **live head** at Execute
      (F6); `downgrade` drops the table.
- [x] Integration test (`tests/integration/test_blocked_udp_port_model.py`): insert a row; duplicate
      `port` → IntegrityError; `port = 70000` and `port = -1` → CheckConstraint violation; user delete
      → `created_by` set NULL.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q` (+ `alembic
      upgrade head` on test DB).
- [x] Test count: `B_cp + ≥3` pass.

**Tests**: integration · **Gate**: full (+ build: `alembic upgrade head`)
**Verify**: `alembic upgrade head` then `\d blocked_udp_port` shows the CheckConstraint + FK.
**Commit**: `feat(control-plane): add blocked_udp_port model + migration`

---

### CT2: `ddos_amplification` service (CRUD + audit + built-in set)

**What**: business logic + audit for the dynamic port list; the read-only hardcoded-set constant.
**Where**: `control-plane/app/services/ddos_amplification.py`
**Depends on**: CT1
**Reuses**: `services/lists.py` + `services/node_control.py` patterns; `services/audit.py::record_event`.
**Requirement**: AMP-02, AMP-03, AMP-05, AMP-07

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `HARDCODED_AMP_PORTS = (17, 19, 53, 111, 123, 137, 161, 389, 520, 1900, 5353, 11211)` with a
      docstring: "mirror of data-plane `amp_port_hardcoded` (blacklist.h) — DP header authoritative;
      change both together" (A-AMP-4).
- [x] `list_blocked_ports(db)` (ordered by `port`); `add_blocked_port(db, actor, port, note)`
      (pre-check → `HTTPException(409, "port already blocked")`; `record_event(action=
      "ddos.amp_port.added", target_type="blocked_udp_port", target_id=str(port),
      metadata={"note": note})`); `remove_blocked_port(db, actor, port)` (404 if absent;
      `record_event(action="ddos.amp_port.removed", ...)`).
- [x] Integration test (`tests/integration/test_ddos_amplification_service.py`): add → row + audit
      event present; duplicate → 409; remove present → gone + audit; remove absent → 404; list ordered.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`.
- [x] Test count: `+≥4` pass.

**Tests**: integration · **Gate**: full
**Verify**: run the new service integration test module → all pass; audit rows visible in `audit_events`.
**Commit**: `feat(control-plane): add ddos amplification blocked-port service + audit`

---

### CT3: `/ddos` admin router + schemas + registration

**What**: admin-only HTTP surface — the tab's backend contract.
**Where**: `control-plane/app/api/routers/ddos.py`, `app/api/schemas/ddos.py`, register in `app/main.py`.
**Depends on**: CT2
**Reuses**: `api/routers/global_blacklist.py` (clone `get_admin_principal`, `_load_actor`, 201/204);
`core/deps` (`require_admin`).
**Requirement**: AMP-04, AMP-06 (+ HTTP surface for AMP-02/03/05)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] Schemas: `BlockedPortCreateRequest{ port: int = Field(ge=0, le=65535), note: str | None =
      Field(default=None, max_length=256) }`; `BlockedPortResponse{ port, note, created_by, created_at
      }`; `AmplificationConfigResponse{ hardcoded_ports: list[int], dynamic_ports:
      list[BlockedPortResponse] }`.
- [x] Router (`prefix="/ddos"`, admin-only every route): `GET /ddos/amplification` → both sets; `POST
      /ddos/amplification/ports` → 201 / 409 / 422; `DELETE /ddos/amplification/ports/{port}` → 204 /
      404; registered in `main.py`.
- [x] Integration test (`tests/integration/test_ddos_router.py`, via `AsyncClient`): admin GET returns
      12 hardcoded + dynamic; POST 201 then duplicate 409; POST `70000` → 422; DELETE 204 then 404;
      **tenant principal → 403** on every route.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`; `python -c
      "import app.main"` import smoke.
- [x] Test count: `+≥5` pass.

**Tests**: integration · **Gate**: full
**Verify**: `pytest -q tests/integration/test_ddos_router.py` all pass; `/openapi.json` lists `/ddos`.
**Commit**: `feat(control-plane): add /ddos amplification admin router`

---

### CT4: `BlockedPortReconciler` + `DpstatBlockedPortsWriter` (worker lane)

**What**: the background lane that converges the BPF bitmap to the PG desired set, fail-safe.
**Where**: `control-plane/app/worker/blocked_port_reconciler.py`
**Depends on**: CT2 (uses `list_blocked_ports` + model)
**Reuses**: `worker/node_control_reconciler.py` verbatim structure (`DpstatBypassWriter` → list arg;
`asserted_bypass` → `asserted_ports: frozenset | None`).
**Requirement**: AMP-08, AMP-09, AMP-10

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `BlockedPortsWriter(Protocol).set(ports: frozenset[int]) -> bool`; `DpstatBlockedPortsWriter`
      (`create_subprocess_exec(binary, "set-blocked-ports", *sorted str ports)`; empty set → no port
      args; False on OSError/timeout/nonzero); `FakeBlockedPortsWriter` (records calls, scripted
      results).
- [x] `BlockedPortReconciler(session_factory, writer, interval_seconds)` with `reconcile_once` (drift
      on `asserted_ports`; on write failure **leave asserted unchanged**, never clear — AMP-10) and
      `run_loop(stop)` (clone `NodeControlReconciler`).
- [x] Integration test (`tests/integration/test_blocked_port_reconciler.py`, `committed_db` +
      `FakeBlockedPortsWriter`): desired set converges (writer receives it); no-drift = no re-write;
      restart (`asserted=None`) re-asserts once; writer failure keeps last-good + retries next tick.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`.
- [x] Test count: `+≥4` pass.

**Tests**: integration · **Gate**: full
**Verify**: `pytest -q tests/integration/test_blocked_port_reconciler.py` all pass.
**Commit**: `feat(control-plane): add blocked-port reconcile lane + dpstat writer`

---

### CT5: worker wiring + `worker_blocked_port_*` settings

**What**: spawn the lane in the worker runtime and expose its knobs.
**Where**: `control-plane/app/worker/worker.py` (+ `app/core/config.py`).
**Depends on**: CT4
**Reuses**: the `node_control`/`nexthop` lane-wiring block in `worker.py` (Protocol + conditional
construct + `create_task(lane.run_loop)`); reuse `worker_telemetry_binary_path` + `_timeout_seconds`.
**Requirement**: AMP-08 (runtime), AMP-13 (no new JobType/Redis)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `config.py`: `worker_blocked_port_enabled: bool = True`, `worker_blocked_port_interval_seconds:
      float = Field(default=1.0, gt=0)` (writer binary/timeout reuse telemetry knobs — T6).
- [x] `worker.py`: `BlockedPortLane` Protocol; construct `BlockedPortReconciler` when
      `settings.worker_blocked_port_enabled`; `create_task(lane.run_loop(stop_event))`; no change to the
      job loop / `process_job` / Redis.
- [x] Integration test (extend `tests/integration/test_worker_runtime.py`): with the lane enabled + a
      `FakeBlockedPortsWriter` and a seeded `blocked_udp_port` row, the running worker converges the
      writer to the desired set; disabling the flag spawns no lane.
- [x] Gate check passes: `ruff check . && ruff format --check . && mypy app/ && pytest -q`.
- [x] Test count: `+≥2` pass.

**Tests**: integration · **Gate**: full
**Verify**: worker runtime test converges; `python -c "import app.worker.__main__"` import smoke.
**Commit**: `feat(control-plane): wire blocked-port reconcile lane into worker`

---

### DT1: `dpstat set-blocked-ports` subcommand [P]

**What**: the privileged writer that materializes the desired port set into `udp_blocked_port_bitmap`.
**Where**: `data-plane/tools/dpstat.c` (+ `data-plane/Makefile` smoke target scaffold for DT2).
**Depends on**: None
**Reuses**: `cmd_set_bypass` skeleton + `open_pinned_map`; `xdpgw-apply.c::apply_inner_fd` idiom;
`loader.c::seed_blocked_port_from_env` word-set idiom.
**Requirement**: AMP-09, AMP-11 (write path)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `#define UDP_BLOCKED_PORT_BITMAP_PIN_PATH PIN_DIR "/udp_blocked_port_bitmap"`; `cmd_set_blocked_
      ports`: parse each argv `0..65535` (else exit 2); build `__u64 words[BLOCKED_PORT_WORDS]` (`words
      [p>>6] |= 1ULL<<(p&63)`); `open_pinned_map(outer)`; for `slot in {0,1}`: inner fd via
      `bpf_map_lookup_elem(outer,&slot,&id)`→`bpf_map_get_fd_by_id(id)`, write all 1024 words, close;
      empty argv → all-zero bitmap; friendly exit 1 when the gateway isn't loaded.
- [x] `usage()` line + `main` dispatch (`strcmp(argv[1], "set-blocked-ports")`).
- [x] Gate check passes: `make bpf skel loader apply dpstat` (builds clean).
- [x] Test count: `B_dp` unchanged (build-only; enforcement is existing dp-unit; writer covered by DT2).

**Tests**: none (build-gated userspace tooling — DP-established `set-bypass`/`set-nexthop` pattern;
verified end-to-end by DT2's privileged smoke, not deferral) · **Gate**: build
**Verify**: `./build/dpstat set-blocked-ports 9999` without pins → friendly "gateway not loaded" error
(exit 1), not a crash; `--help`/usage lists the subcommand.
**Commit**: `feat(data-plane): add dpstat set-blocked-ports subcommand`

---

### DT2: `smoke_blocked_port.sh` privileged veth smoke

**What**: end-to-end proof that a configured port drops on the live data plane and survives an apply.
**Where**: `data-plane/tests/smoke_blocked_port.sh` (+ wired into `make smoke`).
**Depends on**: DT1
**Reuses**: `smoke_bypass.sh` structure (load gateway, veth, crafted frame, assert verdict); the
existing `make smoke` privileged harness.
**Requirement**: AMP-11, AMP-12 (live enforcement)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] Load the gateway (native/DRV), `dpstat set-blocked-ports 9999`, send a UDP frame src-port 9999 →
      `udp_amplification_drop` (idx 7) increments; src-port 9998 → not amp-dropped.
- [x] Assert **both slots** written: after a `xdpgw-apply` service apply (slot flip), src-port 9999
      still drops (carry-forward-safe, AMP-11).
- [x] `dpstat set-blocked-ports` (empty) then src-port 9999 no longer amp-drops (clears cleanly).
- [x] Gate check passes: `make test && sudo make smoke` (green; `smoke_blocked_port.sh` included).
- [x] Test count: `B_dp` (`make test`) unchanged; smoke adds 1 privileged scenario (not in `make test`).

**Tests**: dp-integration · **Gate**: full (`make test && sudo make smoke`)
**Verify**: `sudo make smoke` runs `smoke_blocked_port.sh` and it passes; snapshot shows bit 9999 set.
**Commit**: `test(data-plane): add blocked-port veth smoke`

---

### FT1: amplification config types + hook [P]

**What**: the typed data layer for the tab.
**Where**: `control-plane/frontend/src/api/types.ts` (add DTOs), `src/hooks/resources/
useAmplificationConfig.ts` (+ `.test.ts`).
**Depends on**: None
**Reuses**: `hooks/resources/useGlobalBlacklist.ts` (query + add + remove mutations + invalidate);
`api/client.ts`.
**Requirement**: AMP-16 (data layer for the form)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] `types.ts`: `BlockedPortResponse`, `AmplificationConfigResponse`.
- [x] `useAmplificationConfig()` (`GET /ddos/amplification`), `useAddBlockedPort()` (`POST
      /ddos/amplification/ports`), `useRemoveBlockedPort()` (`DELETE /ddos/amplification/ports/{port}`);
      all invalidate `['amplification-config']`.
- [x] Vitest (`useAmplificationConfig.test.ts`, mocked `apiClient` + `QueryClient`): query maps
      response; add posts body + invalidates; remove hits the port URL + invalidates.
- [x] Gate check passes: `cd control-plane/frontend && npm run lint && npm run typecheck && npm run test
      -- --run && npm run build`.
- [x] Test count: `B_fe + ≥3` pass.

**Tests**: fe-unit · **Gate**: fe
**Verify**: fe gate green; hook test module passes.
**Commit**: `feat(frontend): add amplification config hook + types`

---

### FT2: DDoS Protection page + nav + route [P]

**What**: the named deliverable — the admin tab.
**Where**: `control-plane/frontend/src/features/config/ddos/DdosProtectionPage.tsx` (+ optional
`BlockedPortForm.tsx` + `DdosProtectionPage.test.tsx`); `src/layout/Sidebar.tsx`; `src/App.tsx`.
**Depends on**: FT1
**Reuses**: `features/config/global-blacklist/*` (page/form/test); `ui/*` (DataTable, NumberInput,
Input, ConfirmDialog, Toast, Field); `apiClient` `fieldErrorsFrom422` / `{detail}`.
**Requirement**: AMP-14, AMP-15, AMP-16, AMP-17, AMP-18, AMP-19

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [x] Page: read-only "Built-in blocked source ports (always on)" chips (`hardcoded_ports`) + a
      "Dynamic blocked source ports" `DataTable` (port, note, remove-with-`ConfirmDialog`) + Add form
      (`NumberInput` 0..65535 + `Input` note) surfacing `422`/`409` via `fieldErrorsFrom422` / `{detail}`
      + success `Toast` "Blocked-port list updated; applying to data-plane" (no apply-status indicator).
- [x] `Sidebar.tsx`: admin-group `NavLink to="/admin/ddos"` "DDoS Protection"; `App.tsx`: `<Route
      path="/admin/ddos" element={<DdosProtectionPage />}>` inside `allowedRoles={['admin']}`.
- [x] Vitest (`DdosProtectionPage.test.tsx`, mocked `apiClient`/`QueryClient`): renders built-in +
      dynamic; add success; add 409 inline "already blocked"; remove-with-confirm invalidates; nav item
      hidden for tenant role.
- [x] Gate check passes: `cd control-plane/frontend && npm run lint && npm run typecheck && npm run test
      -- --run && npm run build`.
- [x] Test count: `+≥4` pass.

**Tests**: fe-unit · **Gate**: fe
**Verify**: fe gate green; log in as admin → tab appears + CRUD works against a mocked client; tenant →
no nav item.
**Commit**: `feat(frontend): add DDoS Protection tab (UDP amplification)`

---

### DOC1: docs — dpstat, README, TESTING conventions [P]

**What**: document the new operator surface + test conventions + the loader-reload caveat.
**Where**: `data-plane/README.md`, `control-plane/frontend/README.md`, `.specs/codebase/TESTING.md`.
**Depends on**: DT1, CT5, FT2
**Reuses**: existing README/TESTING structure.
**Requirement**: operability docs (supports AMP-08..19; T4 caveat)

**Tools**: MCP: NONE · Skill: `docs-writer`

**Done when**:
- [x] `data-plane/README.md`: `dpstat set-blocked-ports <p...>` usage + the both-slots/carry-forward
      note + the **drift-only reconcile + loader-reload caveat** (a full loader reload clears the map;
      restart the worker to re-assert — F2/T4).
- [x] `control-plane/frontend/README.md`: the DDoS Protection tab (route, admin-only, built-in vs
      dynamic).
- [x] `.specs/codebase/TESTING.md` deny-stage section: `smoke_blocked_port.sh` + the worker
      `FakeBlockedPortsWriter` convention.
- [x] Gate check passes: `python -c "import app.main"` (docs-only; no code/tests).
- [x] Test count: unchanged (docs only).

**Tests**: none (matrix: docs) · **Gate**: none
**Commit**: `docs: document dpstat set-blocked-ports + DDoS Protection tab`

---

## Phase 5 — P2 (optional; AMP-20/21, flag F3)

### PT1: `dpstat snapshot` blocked-ports read-back

**What**: expose the effective dynamic blocked-port set (active-slot inner, decoded) in `snapshot --json`.
**Where**: `data-plane/tools/dpstat.c` (extend `snapshot`).
**Depends on**: DT1
**Reuses**: `cmd_active_config` slot read + `apply_inner_fd` idiom; existing snapshot JSON assembly.
**Requirement**: AMP-20

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] `snapshot --json` gains a `blocked_ports` array (decode set bits of the active slot's inner).
- [ ] Gate check passes: `make bpf skel loader apply dpstat`; covered by the existing snapshot smoke.
- [ ] Test count: `B_dp` unchanged (build; covered by DT2/snapshot smoke).

**Tests**: none (build; snapshot smoke) · **Gate**: build
**Commit**: `feat(data-plane): expose blocked_ports in dpstat snapshot`

---

### PT2: DDoS Protection effective-state panel [P]

**What**: show the effective blocked set + the `udp_amplification_drop` counter on the tab.
**Where**: `control-plane/frontend/src/features/config/ddos/*` (+ a node-health/snapshot read; reuse
existing telemetry/node-health surface where present).
**Depends on**: FT2, PT1
**Reuses**: existing telemetry/node-health reader for the drop counter; `ui/*`.
**Requirement**: AMP-21 (+ AMP-20 surfacing)

**Tools**: MCP: NONE · Skill: `coding-guidelines`

**Done when**:
- [ ] Panel shows `udp_amplification_drop` (from the existing per-reason telemetry) and, where a read is
      available, the effective vs desired set (drift hint). If no CP read exists for the effective set,
      mark it a `SPEC_DEVIATION` and surface only the counter.
- [ ] Vitest covers the panel render + counter display.
- [ ] Gate check passes: `fe` gate.
- [ ] Test count: `+≥1` pass.

**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(frontend): DDoS Protection effective-state panel`

---

## Parallel Execution Map

```
Phase 0 (concurrent, different infra):
   CT1 (integration)      DT1 [P] (build)      FT1 [P] (fe-unit)

Phase 1 (concurrent):
   CT2 (integration)      DT2 (dp-integration) FT2 [P] (fe-unit)

Phase 2 (serial on compose.test.yml):
   CT3 (integration)  →  CT4 (integration)

Phase 3:
   CT5 (integration)

Phase 4:
   DOC1 [P] (docs)   after DT1, CT5, FT2

Phase 5 (optional P2):
   PT1 (build)  →  PT2 [P] (fe-unit)
```

**Parallelism basis (per TESTING.md):** only **unit / fe-unit** test types are parallel-safe; all CP
tasks here are **integration** → they serialize on the single `compose.test.yml` (one serial CP chain
CT1→CT2→CT3→CT4→CT5). The FE track (`fe` gate) and the DP track (`make`) use independent infra, so
they run **concurrently with** the CP chain. `[P]` marks the parallel-safe track leads/tasks (DT1,
FT1, FT2, DOC1, PT2). DT2 is **dp-integration** (privileged, not parallel-safe within DP) but its
infra is disjoint from CP's compose, so the DP track still overlaps the CP chain in wall-clock.

---

## Pre-Approval Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| CT1 | 1 model + 1 migration (cohesive: schema+DDL) | ✅ Granular |
| CT2 | 1 service module (constant + 3 fns + audit) | ✅ Granular |
| CT3 | 1 router + its schemas + registration | ✅ Granular |
| CT4 | 1 worker module (reconciler + writer + fake) | ✅ Granular (cohesive lane, mirrors nexthop CT2) |
| CT5 | worker wiring + 2 settings | ✅ Granular |
| DT1 | 1 dpstat subcommand | ✅ Granular |
| DT2 | 1 smoke script | ✅ Granular |
| FT1 | types + 1 hook | ✅ Granular |
| FT2 | 1 page (+form) + nav + route | ✅ Granular (cohesive tab, mirrors global-blacklist page) |
| DOC1 | docs across 3 files | ✅ OK (docs, single deliverable) |
| PT1 | 1 dpstat extension | ✅ Granular |
| PT2 | 1 panel | ✅ Granular |

No task spans multiple unrelated components/files. CT4 and FT2 are flagged cohesive-not-split (a lane
= module+writer+fake; a tab = page+nav+route) with precedent (nexthop CT2, global-blacklist page).

## Pre-Approval Check 2 — Diagram ↔ Definition Cross-Check

| Task | Depends on (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| CT1 | None | (root) | ✅ |
| CT2 | CT1 | CT1→CT2 | ✅ |
| CT3 | CT2 | CT2→CT3 | ✅ |
| CT4 | CT2 | CT2→CT4 | ✅ |
| CT5 | CT4 | CT4→CT5 | ✅ |
| DT1 | None | (root) | ✅ |
| DT2 | DT1 | DT1→DT2 | ✅ |
| FT1 | None | (root) | ✅ |
| FT2 | FT1 | FT1→FT2 | ✅ |
| DOC1 | DT1, CT5, FT2 | {DT1,CT5,FT2}→DOC1 | ✅ |
| PT1 | DT1 | DT1→PT1 | ✅ |
| PT2 | FT2, PT1 | {FT2,PT1}→PT2 | ✅ |

No `[P]` task depends on another task in its own phase (Phase 0 leads CT1/DT1/FT1 are mutually
independent; FT2 [P] depends only on FT1 from Phase 0; DOC1 [P] depends only on earlier phases). ✅

## Pre-Approval Check 3 — Test Co-location

| Task | Code layer | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| CT1 | Models + constraints (`db/models.py`, migration) | integration | integration | ✅ |
| CT2 | Service + audit (`services/*.py`) | integration | integration | ✅ |
| CT3 | API router (`api/routers/*.py`) | integration | integration | ✅ |
| CT4 | Worker reconciliation (`worker/*.py`) | integration | integration | ✅ |
| CT5 | Worker runtime (`worker/worker.py`) | integration | integration | ✅ |
| DT1 | dpstat userspace tooling (build) | none (not a verdict path) | none (build) | ✅ (covered by DT2 smoke — DP-established `set-bypass` pattern) |
| DT2 | Live veth enforcement | dp-integration | dp-integration | ✅ |
| FT1 | Frontend hook | fe-unit | fe-unit | ✅ |
| FT2 | Frontend page/route | fe-unit | fe-unit | ✅ |
| DOC1 | Docs | none | none | ✅ |
| PT1 | dpstat tooling (build) | none | none | ✅ (snapshot smoke) |
| PT2 | Frontend panel | fe-unit | fe-unit | ✅ |

All three checks pass — no ❌. The only `Tests: none` entries (DT1, PT1, DOC1) are matrix-consistent:
DT1/PT1 are userspace `dpstat` tooling (not a `BPF_PROG_TEST_RUN` verdict path) verified by the
privileged DT2/snapshot smoke — the DP-established `set-bypass`/`set-nexthop` pattern, **not** test
deferral; DOC1 is docs.

---

## Tools & MCPs

- **Skills**: `coding-guidelines` for every code task; `docs-writer` for DOC1; `mermaid-studio` already
  used for the design diagrams.
- **MCPs**: none (Context7 unavailable in this environment, consistent with prior features).

**Next:** approve tasks → **Execute** (Phase 0: CT1 ∥ DT1 ∥ FT1). Baselines pinned live at the first
Execute gate.
