# State

**Last Updated:** 2026-07-08
**Current Work:** M2 → **Packet parse & fail-fast** — spec + context + design + **tasks APPROVED** (`.specs/features/packet-parse/`, PKT-01..24 → T1–T8; D-PKT-1..4; A-PKT-1..7). **Execute deferred** at user request (approve-only). **Execute tooling chosen:** Skill `coding-guidelines` on C/XDP tasks T1–T7; MCPs none; sub-agents-vs-inline mode TBD at Execute time. Resume = start T1 (scaffold+contracts+trivial prog, build gate). Tasks: **T1** scaffold+contracts (`pkt_meta.h`/`drop_reason.h`)+trivial prog (build gate) · **T2** native/DRV loader `[P]` (build+manual veth) · **T3** `BPF_PROG_TEST_RUN` harness walking-skeleton (dp-unit) · **T4** EtherType+IPv6/unsupported fail-fast+ARP pass · **T5** IPv4+malformed+fragment · **T6** L4+`pkt_meta` fill+service-lookup seam · **T7** VLAN/QinQ (≤2) · **T8** data-plane `TESTING.md` section `[P]`. Only **T2**/**T8** are `[P]` (separate files, no auto-tests); **T3→T4→T5→T6→T7 serialize** on shared `parse.h`/`xdp_gateway.bpf.c`/`test_parse.c`. Req IDs **finalized** in tasks.md (Scaffold/loader/harness 01-06, fail-fast+enum/counter 07-12, pkt_meta 13-18, VLAN 19-22, ARP 23-24). Pre-approval checks (granularity / diagram-cross-check / test-co-location) all pass. Gates: build=`make bpf skel loader`, quick=`make test` (`-DPKT_TEST_HOOKS`), full=+live-veth smoke. First data-plane feature (first C/XDP/eBPF code). Owns the head of the §8.2 pipeline: L2/VLAN/QinQ EtherType resolution, single-parse `pkt_meta` contract, the four fail-fast drops (`ipv6_unsupported`/`unsupported_ethertype`/`malformed_ipv4`/`fragment_unsupported`), ARP classify+`XDP_PASS`, and valid-IPv4 exit at a marked `XDP_PASS` service-lookup seam. **Bootstraps `data-plane/`**: `clang -target bpf` + libbpf-skeleton build, native/DRV-mode loader on `IN` (fail-loud, no silent generic fallback), `BPF_PROG_TEST_RUN` synthetic-packet test harness. **Design:** `data-plane/` layout (`src/{pkt_meta.h,drop_reason.h,parse.h,xdp_gateway.bpf.c}` + `loader/loader.c` + `tests/{pkt_build.h,test_parse.c}`); **inlined stack-`pkt_meta` parse chain** (no tail-call/scratch map — lets next feature inline service lookup by pointer); libbpf skeleton + `bpf_xdp_attach(XDP_FLAGS_DRV_MODE)` fail-loud + `bpf_xdp_query` mode report + signal-detach; test observability via **`test_meta_map` under `-DPKT_TEST_HOOKS`** (production hot path clean); **plain uapi headers, no `vmlinux.h`/CO-RE**; `counter_map` (`PERCPU_ARRAY`) sized `DROP_REASON_CAP=32` (counters feature extends w/o resize); verdict-policy only in `xdp_gateway.bpf.c` (`parse.h` returns status). libbpf APIs (`bpf_prog_test_run_opts`, `bpf_xdp_attach` DRV) **verified vs current docs**. Diagrams rendered (parse-fail-fast flow + component layout). New data-plane `TESTING.md` section (build/quick/full gates, dp-unit parallel-safe) = a Tasks-phase edit. Open (non-blocking, → Tasks): CI kernel/toolchain pin for `BPF_PROG_TEST_RUN`, `eth_proto` endianness, live-veth smoke. No control-plane change. Gray areas AD-013. Next: **Tasks** (Large/Complex).
**Prior M1 work (all awaiting approval → next phase):** **Apply-status state machine** — spec + context + design + **tasks** complete (`.specs/features/apply-status/`, APLY-01..40 → T1–T7; D-APLY-1..3; A-APLY-1..6): `pending→queued→applying→active|failed` behind one guard, API auto-enqueue (real Redis + version-idempotent `AgentJob` ledger via transactional outbox), version-guarded worker-facing `mark_*`, per-service apply-status read API + admin job-list; `agent_job` table + `core/applystate.py` guard + `services/apply.py`; modifies service-rule-list services + `bump_version` (worker loop = M4). **Service, rule & list management (API)** — spec + context + design complete (`.specs/features/service-rule-list/`, SRL-01..44; D-SRL-1..4) → Tasks. **Tenant & CIDR allocation** — spec + design + tasks complete (`.specs/features/tenant-cidr/`, TCA-01..32 → T1–T7) → Execute. **Auth & RBAC** complete (T1–T12, AUTH-01..39) → Execute.

---

## Recent Decisions (Last 60 days)

### AD-013: Packet parse & fail-fast — 4 gray areas (first data-plane feature) (2026-07-08)

**Decision:** (a) **this feature bootstraps `data-plane/`** — `clang -target bpf` + libbpf-skeleton build, a userspace loader that attaches to `IN` in **native/DRV mode** (fail-loud on non-native, **no silent generic fallback**; generic-mode alerting deferred to M6), and a `BPF_PROG_TEST_RUN` test harness — with parse + fail-fast as the payload (mirrors auth-rbac bootstrapping the control-plane); (b) **ARP = classify + `XDP_PASS`** (non-destructive), with a marked seam to switch to `XDP_REDIRECT IN→OUT` when the redirect feature lands (this feature has no `tx_devmap` yet); (c) **valid IPv4 exits at a marked `XDP_PASS` service-lookup seam** with `pkt_meta` fully populated (next feature drops in the service call; tests assert PASS + `pkt_meta` values); (d) **data-plane verified via `BPF_PROG_TEST_RUN` synthetic packets** (tests the real verifier-approved program; no NIC), establishing the data-plane `TESTING.md` pattern. Full context in `.specs/features/packet-parse/context.md` (D-PKT-1..4).
**Reason:** Nothing downstream can compile/load/test without the scaffold; fail-loud-on-non-native keeps the mandatory-native constraint honest; `XDP_PASS` seams keep clean-traffic pass-through demoable without stealing the redirect feature's ownership; `BPF_PROG_TEST_RUN` exercises the loaded object (not a host-compiled mirror) with per-verdict assertions.
**Trade-off:** Large/Complex feature (scaffold + parse in one); ARP/clean-IPv4 only `XDP_PASS` until the redirect feature ships (not a shippable end state alone); `BPF_PROG_TEST_RUN` needs a BPF-capable CI kernel and does not exercise the NIC's native-XDP path (a live-veth smoke gate is a later candidate).
**Impact:** M2 Packet parse feature (PKT-01..24). Creates `data-plane/`; ships shared `enum drop_reason` (4 fail-fast reasons + `map_error`) + a **minimal** per-CPU counter for test observability — full §10.2 coverage + ringbuf sampling + bloom-FP counters remain *Drop-reason counters* (M2#3, A-PKT-3). Adds a data-plane section to `.specs/codebase/TESTING.md` (A-PKT-2). No control-plane change. Assumptions flagged: A-PKT-1 VLAN/QinQ depth = 2, A-PKT-4 L4-truncation = `malformed_ipv4`, A-PKT-5 non-TCP/UDP/ICMP IPv4 continues, A-PKT-7 fragment = MF∨offset.

### AD-012: Apply-status state machine policy — 3 gray areas (2026-07-08)

**Decision:** (a) **auto-enqueue** — every committed service/rule/list mutation immediately creates a job and moves the service `pending→queued`, returning **202** `{apply_status, version, active_version}` (TDD 4.5/4.6); no explicit apply/publish action in v1; (b) **M1 owns machine + guard + real Redis enqueue + `AgentJob` ledger + worker-facing `mark_applying/active/failed` (version-guarded)** — the full machine is unit+integration testable in M1 without a data-plane; M4 adds only the worker loop that calls the mark_* functions; (c) **per-service** apply targets in v1 (status/version/active_version on `ProtectedService`; scoped rule/list edits roll up to the parent service); global-blacklist/feed apply-status deferred to M4 (no generic `ApplyTarget`). Full context in `.specs/features/apply-status/context.md` (D-APLY-1..3).
**Reason:** Auto-enqueue meets ≤5s propagation by construction (idempotent-by-version collapses rapid edits); owning the whole machine now maximises what's verifiable in M1 and hands M4 a clean tested interface; per-service reuses the columns service-rule-list already added — no speculative modeling for targets with no data-plane consumer yet.
**Trade-off:** Redis becomes an enqueue-only dependency one milestone before its consumer; mark_* ship "callable, only called by tests" until M4; N rapid edits enqueue N jobs (superseded via the version guard, not cancelled); a global-blacklist edit gets no own apply-status until M4.
**Impact:** M1 Apply-status feature (APLY-01..40). **Reads** service-rule-list's `version` (A-SRL-3) and **modifies** its service/rule/list services to enqueue (mirrors the tenant-cidr `revoke` modification pattern). New `AgentJob` model + Alembic revision + enqueue-only Redis client. Flagged: A-APLY-1 Redis outage = graceful-degrade via ledger (transactional outbox), A-APLY-3 version guard is the only concurrency control (no job cancellation), A-APLY-6 retry-failed P2 / rollback (OP-05) deferred.

### AD-011: Service/rule/list management policy — 4 gray areas (2026-07-07)

**Decision:** (a) whitelist/blacklist **source** CIDRs are **arbitrary IPv4** (external allowed) — only a service's **destination** `cidr_or_ip` is scoped to `AllocatedCIDR` (AUTH-14); lists are "scoped" = attached to `service_id` (AD-003); (b) **delete service = disable-first, then cascade** its own rules/whitelist/blacklist (dangerous + audited); delete of an `enabled` service → 409; (c) service destination `cidr_or_ip` **must not overlap** another active service — enforced **globally** via partial GiST exclusion (mirrors `AllocatedCIDR`, one dst IP → one service); (d) **manual** global blacklist CRUD ships in this feature (admin-only), **feed** auto-population deferred to M4 (`source` field discriminates `manual`/`feed`). Full context in `.specs/features/service-rule-list/context.md` (D-SRL-1..4).
**Reason:** Whitelisting/blacklisting external sources is the whole point; children are composed by the service (cascade natural) while the CIDR↔service scoping relationship blocks (D-TCA-2); global service-destination no-overlap = deterministic `service_map` + unambiguous ownership; manual global-deny is plain list mgmt, the feed is its own M4 machinery.
**Trade-off:** Delete needs the explicit disable→delete sequence (no one-call live cut); services can't nest destination ranges; plan `committed`/`ceiling` are admin-only in v1 (A-SRL-1, flagged).
**Impact:** M1 Service/rule/list feature (SRL-01..44). Wires auth-rbac `AUTH-14`; realizes tenant-cidr `TCA-16` (revoke-in-use) via the dependency-count hook it stubbed. New `protected_service_active_dest_no_overlap` GiST exclusion + `(service_id,priority)` unique + ≤16-rule cap. Flagged: A-SRL-1 plan authority, A-SRL-3 apply-status handoff (stops at `pending`).

### AD-009: Tenant & CIDR allocation policy — 3 gray areas (2026-07-07)

**Decision:** (a) `AllocatedCIDR` non-overlap enforced **globally** (no two `active` allocations overlap, even within one tenant) via Postgres GiST exclusion constraint partial on `status='active'`; (b) revoking a CIDR still holding services/list entries is **blocked** (409, fail-closed) — not cascade/soft; (c) deleting a tenant with any user or active CIDR is **blocked** until emptied — `suspend` is the reversible off-switch. Full context in `.specs/features/tenant-cidr/context.md` (D-TCA-1..3).
**Reason:** Global no-overlap = one DB constraint + unambiguous scope checks (superset of PRD 7.2). Block-on-in-use / block-on-delete match the product's fail-closed posture; no orphaned users (closes AUTH-36) or silently-unprotected resources.
**Trade-off:** Admin must do explicit multi-step cleanup before revoke/delete (no one-click cascade); global overlap forbids a tenant holding nested ranges of its own.
**Impact:** M1 Tenant & CIDR feature (TCA-01..32). Resolves auth-rbac `AUTH-36`; supplies data + CIDR-scope primitive behind `AUTH-14`. Assumptions flagged: non-canonical CIDR rejected via `cidr` type; `0.0.0.0/0` rejected.

### AD-010: CIDR non-overlap = DB-level partial GiST exclusion (2026-07-07)

**Decision:** `AllocatedCIDR.cidr` uses Postgres `CIDR` type; non-overlap enforced by `EXCLUDE USING gist (cidr inet_ops WITH &&) WHERE (status='active')`. Scope containment (AUTH-14 primitive) via `cidr >>= :target`. API-layer CIDR validation via Python `ipaddress.ip_network(strict=True)` (reject IPv6/host-bits/`0.0.0.0/0`).
**Reason (verified vs PostgreSQL docs, 2026-07-07):** `inet_ops` is a **core built-in** GiST opclass (supports `&&`,`>>`,`>>=`); **no `btree_gist`/extension** needed for a single-column `&&` constraint. Must be **named explicitly** (`ops={'cidr':'inet_ops'}` in SQLAlchemy `ExcludeConstraint`) — it isn't the default opclass until PG 19. Partial `WHERE active` makes soft-revoke free the space (re-allocatable) and is race-proof (concurrent overlaps → one `ExclusionViolation` → 409).
**Trade-off:** requires the named opclass (a known SQLAlchemy gotcha); DB error must be mapped to a friendly 409.
**Impact:** M1 Tenant & CIDR `design.md`; the `cidr_in_tenant_allocation` primitive + `AllocatedCIDR` model are reused by Service/Whitelist/Blacklist (M1/M3). `User.tenant_id` FK to be pinned `ON DELETE RESTRICT`.

### AD-001: Stack for control-plane, DB, dashboard (2026-07-07)

**Decision:** API = Python + FastAPI; DB = PostgreSQL; dashboard = React (SPA). Data-plane (C/XDP/eBPF), worker (Python), queue (Redis) are PRD-fixed.
**Reason:** FastAPI shares Python with the sync worker; Postgres has native inet/cidr for CIDR allocation/overlap; React suits ≤2s realtime dashboards.
**Trade-off:** Two languages (C + Python) across the stack; React adds a build/SPA layer vs server-rendered.
**Impact:** M1 API and M5 dashboards target these; Postgres constraints back `AllocatedCIDR` non-overlap (7.2).

### AD-008: Control-plane testing/runtime conventions (2026-07-07)

**Decision:** Control-plane is **async** (asyncpg + SQLAlchemy 2.0 `AsyncSession`, `redis.asyncio`, httpx `AsyncClient`). Tests use **pytest** with `unit`/`integration` markers; integration tests run against a **docker-compose test stack** (`compose.test.yml` PG+Redis). Quick gate = **ruff + mypy + unit**; full gate adds integration. Conventions in `.specs/codebase/TESTING.md`.
**Reason:** Async fits later realtime dashboards/worker; real PG needed for citext/JSONB/CHECK fidelity; ruff+mypy modern default.
**Trade-off:** Integration tests not parallel-safe (shared compose stack) → mostly sequential execution.
**Impact:** All control-plane code (M1–M6) follows async idioms; only unit-tested tasks can be `[P]`.

### AD-002: Service `disabled` = drop-all (D1 / BL-03) (2026-07-07)

**Decision:** Disabling a service drops all its traffic with reason `service_disabled` (distinct from `service_miss`), requires UI confirm + audit. NOT pass-through.
**Reason:** Inline inbound-only bridge; disable is an intentional protection cut, not a bypass.
**Impact:** M2 pipeline + M1 UI confirm/audit. Refs 6.3, 8.2, 10.2, 12.2.

### AD-003: Whitelist bypass is service-scoped (D2 / BL-01, BL-02) (2026-07-07)

**Decision:** Whitelist/VIP bypass keyed by `service_id`+source CIDR; never edits the global blacklist/feed map. Whitelisting a feed IP raises alert+audit; admin flag can forbid it.
**Reason:** Preserve tenant isolation (5.2) — tenant A must not remove global protection for B/C.
**Impact:** `whitelist_lpm` key includes `service_id` (M3). Refs 6.5, 6.7, 8.3, 12.3.

### AD-004: Allow-rule = first-match by priority, terminal (D3 / BL-05) (2026-07-07)

**Decision:** First enabled rule matching by ascending `priority` decides the verdict; if it is out of quota → `rate_limit_drop`, no fall-through to looser rules.
**Reason:** Fall-through would empty per-rule limits (traffic spills to a looser rule).
**Impact:** M3 rule loop; UI warns on overlapping rules. Refs 6.4, 8.2, 12.2.

### AD-005: Atomic config swap via double-buffer `active_slot` (BL-06) (2026-07-07)

**Decision:** Config maps versioned into 2 slots; worker builds/verifies the inactive slot, then flips `active_slot` in one write. Data-plane pins the slot at ingress. Runtime-state maps are unslotted. Rollback = flip back.
**Reason:** Avoid hybrid new-rule/old-service windows; enables instant rollback (OP-05).
**Impact:** M4 worker + M2 ingress pin. Refs 8.1, 8.3.

### AD-006: Chargeback by p95 clean Gbps (D5 / CM-03, BL-09) (2026-07-07)

**Decision:** Internal chargeback metered on clean (redirected) bandwidth; `billed_gbps = max(committed_clean_gbps, p95_clean_gbps)`; `ServicePlan`/`BillingUsage` model it. Billing bytes from exact per-CPU counters, separate from rate-limited event sampling.
**Reason:** p95 is the bandwidth-billing industry norm; sampling can drop events and must not be trusted for money.
**Impact:** M5 metering + M3 ceiling enforcement (`service_ceiling_drop`). Refs 7.1, 10.3, 8.2/8.3, 12.6.

### AD-007: SLA option (ii) — Availability excluded at Pilot (D6 / CM-01, CM-04) (2026-07-07)

**Decision:** Per-tenant SLA level = high on latency/accuracy/propagation/fairness; Availability deliberately **excluded** from the Pilot SLA (best-effort + maintenance window + bypass in the OLA). HA is the GA condition for an Availability commitment.
**Reason:** Single-node fail-closed inline = SPOF; committed clean bandwidth is guaranteed in hardware terms, availability is not.
**Impact:** M6 bypass/OLA; M7 HA. Refs 3.2, 11.4, 8.4, 14.

---

## Active Blockers

### B-001: No HA / single-node SPOF (CM-01) — GA Blocker

**Discovered:** 2026-07-07 (PRD BA review)
**Impact:** Blocks a production Availability SLA; does NOT block Pilot or development.
**Workaround (Pilot):** OLA documents maintenance window + bypass procedure (OP-03, M6); Availability excluded from Pilot SLA (AD-007).
**Resolution:** Active/passive HA + link bypass (fail-to-wire) at GA — M7.

---

## Lessons Learned

_(none yet)_

---

## Quick Tasks Completed

| # | Description | Date | Commit | Status |
| --- | --- | --- | --- | --- |

---

## Deferred Ideas

Ideas for future features/phases (mostly PRD-tracked GA/Backlog findings):

- [ ] Auto-response / one-click mitigate for fast attack reaction (OP-02, GA) — Captured during: init
- [ ] Monitor/count-only rule mode before enforcing (OP-04, GA) — Captured during: init
- [ ] Sampled per-tenant drop-flow records for self-service debug (OP-06, GA) — Captured during: init
- [ ] `expires_at` reconciliation sweep for whitelist/blacklist (BL-07, GA) — Captured during: init
- [ ] Stateless SYN-cookie / scan detection to back SYN-flood/port-scan claims (BL-04, GA) — Captured during: init
- [ ] PII retention/anonymization for `top_src` (CM-08, GA) — Captured during: init
- [ ] Multi-admin & separation of duties (OP-07, GA) — Captured during: init
- [ ] Guided onboarding + learning mode (OP-08, Backlog) — Captured during: init
- [ ] SSO/IdP + MFA for admin (CM-10, Backlog) — Captured during: init

---

## Todos

Open before Pilot (non-engineering, non-blocking — Product/Legal owned):

- [ ] CM-02: IPv6 hard-drop blackhole warning + checklist in onboarding
- [ ] CM-06: capacity positioning (single 40G node = small/mid scrubber; absorption depends on upstream)
- [ ] CM-07: review threat-feed licenses for commercial/internal-paid use

---

## Preferences

**Model Guidance Shown:** 2026-07-08 (mentioned for spec-pipeline state/doc updates)
**Execute tooling:** Skill `coding-guidelines` on C/XDP code tasks; no MCPs configured; data-plane gates via `make` (build/quick/full).
