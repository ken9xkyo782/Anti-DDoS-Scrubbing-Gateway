# Service-Level Rate-Limit — Tasks

**Tracks:** DP (data-plane C/eBPF) ∥ CP (control-plane Python + SPA), mirroring the nexthop-mac-rewrite
2-track layout. **Cross-track contract = apply wire format v3** (`apply_snapshot.h`, DT1). After DT1 both
tracks run concurrently; final joins = DT4 (live smoke) + the CP→C parse contract test (CT2/DT3).

**Baselines (pin live at Execute):** `B_dp` = `make -C data-plane test` head count; `B_cp` = control-plane
`pytest -q` head count; `B_fe` = `npm run test` head. Each code task keeps totals monotonic and states an
added-test floor. **Tools:** `coding-guidelines` (code), `docs-writer` (CT5). No MCPs.

**Reqs mapped:** SVR-01..11 all covered (see each task).

Legend: `[P]` = parallel-safe (disjoint files). Every task: *What / Where / Depends / Reuses / Done-when /
Tests / Gate*.

---

## Phase 0 — contract + CP schema (parallel)

### DT1 — Wire format v3 (contract anchor) · reqs SVR-06
- **What:** bump `APPLY_SNAPSHOT_SCHEMA_VERSION 2→3`; insert `service_pps le64, service_bps le64,
  svc_rl_flags u8` into the service record (after `vip_flags`, before `rule_count`);
  `APPLY_SNAPSHOT_SERVICE_FIXED_SIZE 50→67`; `RULE_SIZE` stays 10; rewrite the commit-`49b7c91` note
  (per-rule pps/bps removed entirely; rate-limit now in the service record).
- **Where:** `data-plane/src/apply_snapshot.h`.
- **Depends:** none. **Reuses:** existing vip field encoding as the template.
- **Done when:** header compiles; constants consistent; note accurate.
- **Tests:** none (constants); covered by DT2/DT3 build + CT2 golden + DT4 smoke.
- **Gate:** `make -C data-plane bpf` compiles.

### CT1 — Models + migration · reqs SVR-02, SVR-07
- **What:** `ProtectedService += service_pps, service_bps` (`BigInteger`, nullable);
  `AllowRule -= pps, bps`. Alembic revision off current head: add the two service columns, drop the two
  rule columns (downgrade reverses; old rule values discarded — documented in the revision docstring).
- **Where:** `control-plane/app/db/models.py`, `control-plane/migrations/versions/*`.
- **Depends:** none. **Reuses:** `vip_pps/vip_bps` column definitions as the template.
- **Done when:** `alembic upgrade head` + `downgrade -1` clean on the test DB; models import.
- **Tests:** migration up/down test; model-shape unit (`+2`).
- **Gate:** `ruff && mypy && pytest -q tests/…/test_migrations* tests/…/models*`.

---

## Phase 1 — data-plane core ∥ CP serialize/API

### DT2 — Maps + hot-path stage + per-rule removal · reqs SVR-01, SVR-03, SVR-04, SVR-08, SVR-09
- **What:** add `struct svc_rl_config` (=layout of `vip_config`), `svc_rl_config_map` (slotted) +
  `svc_rl_state` (unslotted PERCPU `rl_bucket`), and `svc_rl_admit()` (clone `vip_bucket_admit`, reuse
  `rl_burst`). Wire into `rules.h::allow_rule_stage`: on rule match → `svc_rl_admit(service_id,…)` →
  fail = `record_drop(DR_RATE_LIMIT_DROP)`, else `admit_clean`. **Remove** `rule_entry.pps/bps`,
  `RULE_F_PPS_SET/_BPS_SET`, `rate_limit_state`, `rl_key`, `rl_config`, `rl_bucket_admit/consume/refill/
  reset`; update `rule_entry`/`rule_block` size asserts. Keep `rl_bucket` + `rl_burst`. **Task-0 grep**
  first for stragglers referencing deleted symbols (re-home `test_no_refill` if VIP/dp-unit share it).
- **Where:** `data-plane/src/rules.h`, `whitelist.h` (pattern ref), new `svc_ratelimit.h` (or inline in
  rules.h — match ARL/WLV file convention), `xdp_gateway.bpf.c` seam.
- **Depends:** DT1. **Reuses:** `vip_bucket_*`, `rl_burst`, `rl_bucket`, `record_drop`, `active_slot`.
- **Done when:** program loads native + verifier-clean; over/under-budget verdicts correct; NULL flags =
  admit; whitelist path unaffected.
- **Tests (`make test`, BPF_PROG_TEST_RUN):** service over-budget→`rate_limit_drop`; under→`clean`;
  pps-only; bps-only; both-NULL→never limited; VIP hit unaffected; updated size asserts. Amend the ARL
  34-case suite (drop per-rule-rate cases). Floor: net **≥ B_dp** (removed cases offset by new).
- **Gate:** `make -C data-plane test` green; native load + verifier probe.

### CT2 — Applier serialization v3 + golden · reqs SVR-06, SVR-01
- **What:** `ServiceConfig` snapshot `+= service_pps/service_bps`; `AllowRule` snapshot `−= pps/bps`; add
  `_svc_rl_flags()` (mirror `_vip_flags`); pack string `"<I4sIBBBQQQQBH"` → `"<I4sIBBBQQQQBQQBH"`
  (append `service_pps or 0, service_bps or 0, _svc_rl_flags(service)` before `rule_count`). Regenerate
  the golden fixture for v3.
- **Where:** `control-plane/app/worker/applier.py`, `tests/unit/test_snapshot_serialize.py` + fixture.
- **Depends:** DT1 (byte layout), CT1 (model fields). **Reuses:** vip pack path.
- **Done when:** golden fixture matches; bytes align with DT1’s `SERVICE_FIXED_SIZE=67`, rule still 10 B.
- **Tests:** regenerated golden equality + a field-level assert on the new offsets (`+`≥1).
- **Gate:** `ruff && mypy && pytest -q tests/unit/test_snapshot_serialize.py`.

### CT3 — API schemas + service service · reqs SVR-02, SVR-04, SVR-10
- **What:** service create/patch schemas `+= service_pps/service_bps: int|None` (`ge=0`); AllowRule
  schemas `−= pps/bps`; `create_service`/`update_service` persist the two service fields (mirror
  `vip_pps/vip_bps`); rule create/replace stops reading pps/bps.
- **Where:** `control-plane/app/api/schemas/services.py`, `app/api/routers/services.py`,
  `app/services/services.py`.
- **Depends:** CT1. **Reuses:** vip_pps/vip_bps request+service-layer handling.
- **Done when:** POST/PATCH service round-trips the fields; posting rule pps/bps is rejected/ignored;
  validation rejects negatives.
- **Tests:** api round-trip (service RL set/clear), schema-validation (ge=0, NULL), rule schema no-pps/bps.
  Floor `+`≥3.
- **Gate:** `ruff && mypy && pytest -q` (service/rule router + schema suites).

---

## Phase 2 — apply reader/loader ∥ SPA ∥ docs

### DT3 — `xdpgw-apply` v3 read + loader pin/seed · reqs SVR-06, SVR-08
- **What:** `parse_service` reads `service_pps/service_bps/svc_rl_flags` (after vip), validates; build +
  write the `svc_rl_config` inner (clone the `vip_config` write); add `svc_rl_config_map` to the slotted
  rebuild/flip + structural read-back; accept schema v3 (reject v2). Loader: pin `svc_rl_config_map` +
  `svc_rl_state`; optional `XDPGW_SEED_SVC_PPS/BPS` seed (mirror VIP seed).
- **Where:** `data-plane/tools/xdpgw-apply.c`, `data-plane/loader/loader.c`.
- **Depends:** DT1, DT2. **Reuses:** vip_config write/rebuild, loader vip pin/seed lines.
- **Done when:** a v3 snapshot applies + reads back; maps pinned; seed writes a usable row.
- **Tests:** `test_parse.c`/`test_snapshot.c` gain v3 service-RL field asserts (mirror vip asserts);
  build-gated tooling covered live by DT4.
- **Gate:** `make -C data-plane apply loader test`.

### DT4 — Live veth smoke · reqs SVR-03, SVR-05, SVR-11 (verify)
- **What:** privileged veth smoke: seed `svc_rl_config` (pps+bps), drive traffic; assert under-budget
  `clean` rises, over-budget `rate_limit_drop` rises; a whitelisted source on the same service is
  unaffected (VIP only); both-unset → zero `rate_limit_drop`.
- **Where:** `data-plane/tests/` smoke (mirror the fairness/VIP live smoke + `dpstat` counter reads).
- **Depends:** DT2, DT3. **Reuses:** veth XDP smoke harness, `dpstat counters`.
- **Done when:** all four assertions pass on veth (native).
- **Tests:** dp-integration (privileged, run as root per gate-running-environment memory).
- **Gate:** full dp smoke green.

### CT4 — SPA forms · reqs SVR-10 · [P]
- **What:** service form `+=` rate-limit inputs (mirror VIP inputs, NULL-clearable); rule form `−=` pps/bps.
- **Where:** `control-plane/frontend/src/features/config/…` (service + rule forms), resource hooks/DTOs.
- **Depends:** CT3 (API field contract). **Reuses:** VIP input components, `fieldErrorsFrom422`.
- **Done when:** create/edit service sets/clears RL; rule form has no rate fields; typecheck+build clean.
- **Tests:** fe-unit for the service form field + rule form absence. Floor `+`≥2.
- **Gate:** `npm run lint && typecheck && test --run && build`.

### CT5 — Docs + memory · reqs SVR-11 · [P]
- **What:** update `data-plane/README.md` (rewrite the "Allow rules" per-rule-RL paragraph → service RL),
  PRD §6.4, ARL `spec.md` amendment (per-rule rate-limit removed → SVR), and project memory
  `[[allow-rule-pps-bps-flags-blackhole]]` (mark resolved-by-SVR). Confirm the `apply_snapshot.h` note
  (DT1) reads correctly.
- **Where:** `data-plane/README.md`, `PRD.md`, `.specs/features/allow-rule-ratelimit/spec.md`, memory.
- **Depends:** DT1 (note), conceptually after DT2/CT3 land. **Reuses:** `docs-writer` skill.
- **Done when:** docs describe one service rate-limit; no stale per-rule-RL claims remain.
- **Tests:** none. **Gate:** docs-writer review; `grep -ri "per-rule.*rate" docs data-plane` clean.

---

## Execution order & parallelism
```
Phase 0:  DT1  ∥  CT1
Phase 1:  DT2 (←DT1)  ∥  CT2 (←DT1,CT1)  ∥  CT3 (←CT1)
Phase 2:  DT3 (←DT1,DT2)  ∥  CT4 (←CT3) [P]  ∥  CT5 [P]
Phase 3:  DT4 (←DT2,DT3)          # live join; CP↔C parse contract asserted in CT2+DT3
```
**Deploy note (A-SVR-3):** single maintenance step — Alembic migrate + worker(applier v3) + `xdpgw-apply`
v3 + loader reload together (no rolling v2/v3 mix). Pre-approval checks (granularity / diagram↔depends /
test co-location) to run before Execute, per repo convention.
