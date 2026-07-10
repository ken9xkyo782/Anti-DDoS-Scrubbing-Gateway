# Roadmap

**Current Milestone:** M1 — Control-plane foundation & tenant model
**Status:** Planning

> All M1–M6 milestones together constitute the **Pilot MVP v1**. M7 is the **GA** track. Milestones are dependency-ordered; features are the units taken through Specify → (Design → Tasks) → Execute.

---

## M1 — Control-plane foundation & tenant model

**Goal:** Auth, RBAC, tenant isolation, and full config CRUD persisted to Postgres with an apply-status state machine — config manageable end-to-end before the data-plane enforces it.
**Target:** Admin/tenant can log in, allocate CIDRs, and CRUD services/rules/lists; every write is tenant-scoped and audited.

### Features

**Auth & RBAC** - IN PROGRESS (spec drafted)
- Session auth, password hashing (argon2/bcrypt), `admin` + `tenant_user` roles
- Fail-closed authorization; tenant ownership checks on every write
- Spec: `.specs/features/auth-rbac/spec.md` (AUTH-01..39)

**Tenant & CIDR allocation** - IN PROGRESS (spec + design + tasks)
- Tenant CRUD; `AllocatedCIDR` with **global** non-overlap constraint (Postgres GiST exclusion)
- Admin allocates/revokes ranges; usage & overlap-check views; reusable CIDR-scope primitive
- Resolves auth-rbac `AUTH-36` (delete-tenant rule); provides data + primitive behind `AUTH-14`
- Spec `spec.md` (TCA-01..32); context `context.md` (D-TCA-1..3); `design.md`; `tasks.md` (T1–T7)
- Requires auth-rbac executed first (reuses its skeleton/guards/audit)

**Service, rule & list management (API)** - IN PROGRESS (spec + context + design drafted)
- `ProtectedService` + `ServicePlan` (committed/ceiling clean Gbps) CRUD, dest `cidr_or_ip` ⊆ `AllocatedCIDR` (wires AUTH-14) + global no-overlap across active services
- `AllowRule` (≤16, unique priority, first-match warn), whitelist/VIP + service/global blacklist CRUD; list sources are arbitrary IPv4 (service-scoped, not source-in-allocation)
- Disable = drop-all + confirm + audit (AD-002); delete = disable-first + cascade children; realizes tenant-cidr `TCA-16` (revoke-in-use blocked)
- Spec `spec.md` (SRL-01..44); context `context.md` (D-SRL-1..4, A-SRL-1..6); `design.md` + rendered diagrams
- Requires auth-rbac + tenant-cidr executed first (reuses guard/audit + `cidr_in_tenant_allocation`/`AllocatedCIDR`/`core/cidr`)

**Apply-status state machine** - IN PROGRESS (spec + context + design + tasks)
- `pending → queued → applying → active | failed` behind a single guarded transition function (illegal / backward-`active_version` transitions rejected); a `failed` apply keeps the last-good `active_version` live
- **Auto-enqueue** (D-APLY-1): every committed service/rule/list mutation creates a durable version-idempotent `AgentJob`, moves the service `pending→queued`, returns **202** `{apply_status, version, active_version}` (TDD 4.5/4.6)
- **Worker-facing** `mark_applying/active/failed` (version-guarded → "no stale-over-new swap"); the whole machine is testable in M1 without a data-plane, M4's worker just calls them
- **Per-service** targets in v1 (D-APLY-3); per-service read API (9.2) + admin job/backlog list; reads service-rule-list's `version` (A-SRL-3) and modifies its service/rule/list services to enqueue
- Spec `spec.md` (APLY-01..40); context `context.md` (D-APLY-1..3, A-APLY-1..6); `design.md` + rendered diagrams (component + state-machine + enqueue-apply sequence)
- Design: `agent_job` table (`UNIQUE(target,version)` idempotency, `ON DELETE CASCADE`); pure `core/applystate.py` guard; `services/apply.py` (transactional-outbox enqueue + version-guarded `mark_*` + retry + reads + `ApplyDispatcher`); modifies service-rule-list services + `bump_version`
- Tasks `tasks.md` (T1–T7; all 40 reqs mapped): T1 guard (unit, `[P]`) · T2 model+migration · T3 enqueue outbox+dispatcher+reads · T4 version-guarded `mark_*`+retry · T5 read/retry/jobs router · T6 wire enqueue into SRL services · T7 SRL routers→202
- Requires service-rule-list executed first; adds enqueue-only Redis + `AgentJob` model (worker loop = M4)

---

## M2 — Data-plane verdict pipeline (XDP core)

**Goal:** Native XDP on `IN` that parses, fail-fast drops unsupported traffic, matches services, and redirects clean traffic to `OUT` as a header-preserving L2 bridge.
**Target:** Clean IPv4 traffic to a declared/enabled service forwarded `IN→OUT`; unsupported traffic dropped with correct reasons; per-CPU counters populated.

### Features

**Packet parse & fail-fast** - VERIFIED (executed)
- L2/VLAN/QinQ EtherType, IPv4+L4 parse into `pkt_meta` (single parse)
- Drops: `ipv6_unsupported`, `unsupported_ethertype`, `malformed_ipv4`, `fragment_unsupported`; ARP = classify + `XDP_PASS` (redirect seam deferred)
- **Bootstraps `data-plane/`**: `clang -target bpf` + libbpf-skeleton build, native/DRV-mode loader on `IN` (fail-loud, no generic fallback), `BPF_PROG_TEST_RUN` test harness; valid IPv4 exits at a marked `XDP_PASS` service-lookup seam
- Ships shared `enum drop_reason` + minimal per-CPU counter (full §10.2 set + sampling = *Drop-reason counters*); adds data-plane test conventions to `TESTING.md`
- Spec `spec.md` (PKT-01..24); context `context.md` (D-PKT-1..4, A-PKT-1..7); `design.md` + rendered diagrams (parse-fail-fast flow + component layout); `tasks.md` (T1–T8, all 24 reqs mapped)
- Design: `data-plane/` layout (`pkt_meta.h`/`drop_reason.h`/`parse.h`/`xdp_gateway.bpf.c` + `loader/loader.c` + `tests/`); inlined stack-`pkt_meta` parse chain (no tail-call/scratch map); libbpf skeleton + `bpf_xdp_attach(DRV)` fail-loud; `BPF_PROG_TEST_RUN` tests (`-DPKT_TEST_HOOKS` `test_meta_map`); plain uapi headers (no `vmlinux.h`); `counter_map` sized `DROP_REASON_CAP=32`. libbpf APIs verified vs docs.
- Tasks: **T1** scaffold+contracts+trivial prog (build) · **T2** native loader `[P]` · **T3** `BPF_PROG_TEST_RUN` harness · **T4** EtherType+IPv6/unsupported+ARP · **T5** IPv4+malformed+fragment · **T6** L4+`pkt_meta`+seam · **T7** VLAN/QinQ · **T8** TESTING.md data-plane section `[P]`. Only T2/T8 `[P]`; T3→T7 serialize on shared files. Establishes data-plane `TESTING.md` conventions (T8).
- First data-plane feature — no control-plane change; consumed by *Service lookup & transparent redirect* (replaces both seams) and all of M3

**Service lookup & transparent redirect** - IN PROGRESS (spec + context + design + tasks APPROVED; Execute deferred)
- `service_map` match (LPM by dst IPv4); `service_miss` vs `service_disabled` (drop-all, not pass-through)
- `XDP_REDIRECT IN→OUT` via `tx_devmap`, TTL/checksum preserved (verbatim frame, no L3 mutation)
- `active_slot` snapshot/pin at ingress (consistent per-packet view); first **config maps** + slot pin
- Replaces packet-parse's two seams (service-lookup + ARP); **ARP now redirects `IN→OUT`** (D-SLRD-3)
- Owns the config-map **read/pin side** + a userspace seed helper; DB build + **atomic swap** = M4 (D-SLRD-1)
- Verified by `BPF_PROG_TEST_RUN` (decision via `test_meta_map`) + a gated live two-veth smoke (TTL/csum, D-SLRD-2)
- Spec `spec.md` (SLRD-01..26); context `context.md` (D-SLRD-1..3, A-SLRD-1..8); `design.md` + rendered diagrams (verdict flow + config-map architecture); `tasks.md` (T1–T7, all 26 mapped)
- Design (AD-015): `service_map` = `ARRAY_OF_MAPS`[2] of `LPM_TRIE` inners (double-buffer) + `active_config` + `tx_devmap`; hot-path slot-pin → LPM → verdict → `bpf_redirect_map(&tx_devmap,0,XDP_DROP)` (fail-closed); adds `DR_SERVICE_MISS`/`DR_SERVICE_DISABLED` + `pkt_meta.{service_id,active_slot,verdict}`; extends loader (`OUT`+seed) & migrates the 21 parse tests' verdict expectations. 3 kernel semantics web-verified.
- Tasks: **T1** contract headers (build) · **T2** config maps + **load de-risk** (map-in-map/LPM feasibility here, else fallback) · **T3** service seam (pin+LPM+verdicts+redirect+tests+migrate IPv4 tests) · **T4** ARP redirect seam · **T5** loader `OUT`+populate+seed `[P]` · **T6** live-veth smoke (dp-integration, TTL/csum) · **T7** TESTING.md. Only **T5** `[P]`; T3→T4 serialize on shared files; T6 not parallel-safe.
- Requires packet-parse executed first (**satisfied** — packet-parse VERIFIED); reuses `pkt_meta`/`drop_reason`/loader/`BPF_PROG_TEST_RUN`

**Drop-reason counters** - VERIFIED (executed)
- Full §9.2 16-reason `enum drop_reason` as **frozen index ABI** (§9.2 doc order 0..15; one-move migration `map_error` 4→15; name table in header = source of truth; append-only growth within `DROP_REASON_CAP=32`)
- Exact lock-free per-CPU `counter_map` for every reason (9 M3 reasons = enum+slot only, read 0 until wired); fail-closed on bad reason (`map_error`); reset-on-reload documented
- Rate-limited ringbuf/perf drop-event sampling (reason + pkt context; hard events/sec budget; safe with no reader; suppression observable; counters exact regardless) + P3 operator CLI (dump + sample tail)
- Spec `spec.md` (DRC-01..17); context `context.md` (D-DRC-1: numbering); `design.md` + rendered diagrams (drop-path flow + component/map layout)
- Design (AD-017): sampling = **ringbuf** (256 KiB, non-blocking reserve, fail-open to `LOST`) + per-CPU token bucket with runtime-tunable `sample_config` (defaults 256/s, burst 64 per CPU); `record_drop(meta, reason)` fuses count+sample; maps pinned `/sys/fs/bpf/xdp_gateway/`; new `tools/dpstat` CLI (counters/tail/rate); `sample_stats` separate from the counter ABI. 3 kernel semantics web-verified; **test_run→ringbuf delivery succeeded** in the de-risk case.
- Tasks `tasks.md` (T1–T6; all 17 reqs verified): **T1** ABI freeze + `drop_event.h` + DRC-04 case · **T2** `sample.h` ringbuf/bucket + fused `record_drop(meta,r)` · **T3** ringbuf de-risk + budget/content/fail-closed cases · **T4** loader pin `/sys/fs/bpf/xdp_gateway/` + seed · **T5** `tools/dpstat` · **T6** TESTING.md/README. Baseline **B=29**; T1/T2 = 30; T3/T6 = 34.
- Final gates: `make test` → 34 passed; `make bpf skel loader dpstat` passed; `./build/dpstat counters` without pins returns a friendly gateway-not-loaded error. Requires SLRD executed first (satisfied); no control-plane change.
- Requires packet-parse (VERIFIED); intended after service-lookup-redirect Execute (slots 5/6 already §9.2-correct). Out of scope: M3 drop paths, per-service/billing counters, `bloom_hit_lpm_miss`, worker aggregation

---

## M3 — Policy enforcement & fairness

**Goal:** Full verdict pipeline — allow-rules, rate-limits, scoped whitelist/VIP, blacklists, amplification/bogon filters, and per-service committed clean-bandwidth reservation.
**Target:** Pipeline of section 8.2 fully enforced; fairness test passes (flooding service A never starves service B's committed bandwidth).

### Features

**Allow-rule matching & rate-limit** - VERIFIED (executed)
- First-match by ascending `priority`, terminal verdict, early-exit on `rule_count`; no match (incl. zero rules) = `not_allowed` — enabled services become **default-deny**
- Per-CPU aggregate token buckets (`rate_limit_state`, unslotted); `rate_limit_drop`, no fall-through; NULL quota = unlimited, 0 = block
- Slotted `rule_block_map` (≤16/service, pinned-slot read, fail-closed `map_error`); wires frozen ABI indices 9/10; seed-helper interim writer (D-SLRD-1 posture); migrates the 34-case suite; marked admit→redirect seam for M3 #4
- Spec `spec.md` (ARL-01..25; A-ARL-1..8); context `context.md` (**D-ARL-1** strict `any` = {tcp,udp,icmp} — other IPv4 protos always `not_allowed`, no tunnel/IPsec in v1; **D-ARL-2** buckets reset on config swap; AD-018); `design.md` + rendered diagrams (rule-stage flow + map layout)
- Design (AD-019): `src/rules.h` = stage + M4 build contract; blocks **pre-sorted asc priority** (position = match order, no `priority` in kernel); lazy version-reset `PERCPU_HASH` buckets (zero worker plumbing); **rate÷nCPU** split via rodata `rl_ncpus` (node admit never exceeds configured rate); `bps` map unit = bytes/sec; `rl_config.test_no_refill` + CPU-pinned runner = deterministic dp-unit buckets. Kernel semantics web-verified (per-CPU-hash current-CPU access + zero-fill on create)
- Tasks `tasks.md` (T1–T5; all 25 reqs mapped): **T1** contracts+maps+verifier de-risk (map-in-map HASH inner + bounded loop proven at load, fallback documented) · **T2** match engine + wire-in + 34-case migration · **T3** per-CPU buckets + lazy version reset + deterministic mode · **T4** loader match-all seed + `rl_ncpus` + live smoke (full gate) · **T5** TESTING.md/README `[P]`. Baseline **B=34**; T2 ≥42; T3 ≥49
- Requires SLRD + drop-reason counters executed (both VERIFIED); rule shape mirrors SRL `allow_rule` (contractual, no DB read)
- Executed T1–T5 (all 25 reqs verified); final gates: `make test` → **50 passed**; `make test && sudo make smoke` green; enabled services now default-deny; TESTING.md rule-stage conventions + README tunnel note landed

**Whitelist/VIP (scoped) & VIP ceiling** - IN PROGRESS (spec APPROVED + context + design drafted)
- Bloom → LPM keyed by `service_id`+source CIDR (no cross-service bypass, BL-01/02); bloom = guard only (FP cost-only, no false negatives); hit bypasses rule stage + future M3#3 filters, miss continues unchanged
- VIP ceiling aggregate per-service bucket (`vip_pps`/`vip_bps`, per-CPU, unslotted `vip_ceiling_state`); over-ceiling = terminal `vip_ceiling_drop` wiring frozen ABI index 14; VIP branch skips the 8.4 admit ladder
- Slotted whitelist config maps = M4 build contract; seed-helper interim writer (D-SLRD-1); marked seams for M3#3 (miss path) + M3#4 (ingress cap before whitelist)
- Spec `spec.md` (WLV-01..25); context `context.md` (**D-WLV-1**: NULL `vip_pps`+`vip_bps` = whitelist **inactive** — fail-safe BL-08 reading; one set dimension governs alone; `0` = explicit block; A-WLV-1..8)
- Design (AD-021): composite scoped LPM key `{svc_be32, src_be32}` prefixlen ≥32 (BL-02 by key construction); bloom = /24 buckets + per-service `WL_F_HAS_BROAD` always-LPM escape; `service_val.wl_flags` pad byte = zero-cost D-WLV-1 gate; slotted `vip_config_map` + VIP bucket reusing `rl_bucket`/helpers verbatim (`rules.h` untouched); VIP admit → `redirect_out()` directly (not `admit_clean`, §8.4.6); bloom inners replace-only (M4 contract). Bloom-as-static-inner de-risk ladder: BTF static → loader-created → LPM-only. 3 kernel semantics web-verified (bloom push/peek + inner-map opt-in, LPM 64-bit composite key)
- Tasks `tasks.md` drafted (T1–T5; all 25 reqs mapped, baseline **B=50**): **T1** contracts+maps+bloom-composition de-risk (51) · **T2** scoped match stage+wire-in+seams (≥60) · **T3** VIP ceiling bucket, terminal idx 14 (≥66) · **T4** loader env-driven seed+live smoke (full gate) · **T5** docs `[P]`. Only T5 parallel (T1–T3 share files; T4 smoke not parallel-safe)
- ARL executed → **A-WLV-8 execute gate satisfied**; reuses AD-019 bucket/determinism patterns; design + tasks APPROVED → next: **Execute**

**Blacklist (bloom + LPM)** - VERIFIED (executed; `make test` → 91 passed)
- Global + service blacklist via bloom → LPM at WLV seam B (whitelist-miss path); global = all services, service = scoped by `service_id` key (BL-02 posture); wires frozen ABI indices 4/7/8 (`bogon_drop`/`udp_amplification_drop`/`blacklist_drop`); global maps sized to the 1M-entry envelope
- Hardcoded UDP amplification ports (compile-time **full set incl. 53/123** — D-BLK-1; resolver/NTP tenants whitelist upstreams), bogon check (compile-time IANA set — forces documented dp-unit source migration off RFC 5737), dynamic blocked-port bitmap (slotted config; **seed-only v1 writer** — D-BLK-2, control-plane writer deferred)
- `bloom_hit_lpm_miss` exact per-CPU counter outside `counter_map` (covers whitelist + both blacklist blooms; dpstat gains a new surface); whitelist hit bypasses the whole stage (§6.5 VIP exception)
- Spec `spec.md` (BLK-01..26); context `context.md` (D-BLK-1..2, A-BLK-1..8 — AD-022); `design.md` + rendered diagrams (deny-stage flow + map layout)
- Design (AD-023): pure-code amp/bogon checks; global bloom = /24 buckets + 16..23 expansion band + slot-level `GBL_F_HAS_BROAD` escape + builder fill invariant; 1M LPM footprint measured at gated `make blbulk`; service pair = AD-021 verbatim gated by `service_val.bl_flags` pad byte; bitmap = ARRAY inner 1024×u64; per-stage `bloom_stats` PERCPU_ARRAY[3] + dpstat section (bump only when bloom consulted); `pkt_meta.bl_state`; BLK-24 migration via named non-bogon source constants. 3 kernel semantics web-verified (bloom 7/5 sizing; LPM NO_PREALLOC/kmalloc + ~670ns @1M Cloudflare; ARRAY inner)
- Tasks `tasks.md` drafted (T1–T8; all 26 reqs mapped, baseline **B=68**): **T1** contracts+maps+1M load de-risk (68 unchanged) · **T2** `[P]` bogon-space suite migration (verdict-neutral, 68 unchanged) · **T3** amp/bogon/bitmap + seam-B wire (≥78) · **T4** blacklist bands + exact `bloom_stats` (≥88) · **T5** loader seed+smoke (full) · **T6** dpstat FP section · **T7** gated `blbulk` 1M + footprint · **T8** docs. Only T2 parallel; T5/T7 privileged
- Requires WLV executed first (**satisfied** — WLV VERIFIED); consumes SRL `BlacklistEntry` rows contractually (maps = M4 build contract)

**Fairness & bandwidth reservation (8.4)** - IN PROGRESS (spec + context + design APPROVED + tasks drafted)
- 2-tier committed (global + `bpf_spin_lock`, exact) / burst (per-CPU, `ceiling−committed`) buckets per service at the **ARL-24 seam** (`admit_clean()`); burst dual-draws the node headroom bucket (`capacity−Σcommitted`, floor 0); drops `service_ceiling_drop`/`congestion_drop`; VIP never enters the ladder (§8.4.6 structural)
- Ingress-cost cap at **WLV-24 seam A** (pre-whitelist, destination-keyed spoof-immune): dual bps + derived-pps budget = `k×ceiling`, **k=3** default, ref packet size ~512 B node-tunable (**D-FAIR-1**); over-cap = early `ingress_cap_drop`; VIP traffic subject to the cap (documented precedence)
- Wires the last 3 frozen ABI indices **11/12/13** — all 16 §9.2 reasons live; per-service rates via a **new** slotted config map from `ServicePlan` (M4 build contract, A-FAIR-2); 3 runtime maps unslotted; `node_clean_capacity` = env-driven seed, 40 Gbps §15 default when unset (**D-FAIR-2**)
- Deterministic fairness scenario = the **M3 milestone gate** (flood A → B's committed admits 100%, FAIR-24); spin-lock-in-XDP de-risked fail-fast with fallback (FAIR-22); default seed keeps post-BLK baseline verdict-identical
- Spec `spec.md` (FAIR-01..27); context `context.md` (D-FAIR-1..2, A-FAIR-1..8 — AD-024); `design.md` + rendered diagrams (ladder flow + map layout)
- Design (AD-025): new `src/fairness.h` (both stages + 2 slotted config maps + 4 runtime bucket maps); committed = top-level HASH + BTF `bpf_spin_lock` (now-before-lock, pure-ALU CS; fallback → `__sync` atomics → per-CPU split); burst/node/cap reuse `rl_bucket`/helpers verbatim; budgets precomputed userspace (k/ref-pkt/capacity = env only); `pkt_meta` first deliberate growth 32→40 (`fair_state`); `FAIR_RATE_MAX` 16e9 B/s overflow clamp. 3 kernel semantics web-verified (spin_lock in XDP: program types, map homes, CS rules)
- Tasks `tasks.md` (T1–T6; all 27 reqs mapped, baseline **B=91** pinned live): **T1** contracts+maps+pkt_meta growth+spin-lock de-risk (92) · **T2** ingress-cap stage+seam A (≥99) · **T3** admit ladder at `admit_clean` (≥107) · **T4** fairness scenario = M3 gate (≥110) · **T5** loader env seed+fairness smoke (full gate) · **T6** docs `[P]`
- Blacklist-filters executed (A-FAIR-1 satisfied); completes the §8.2 pipeline

---

## M4 — Worker sync & threat feed

**Goal:** Python worker consuming Redis jobs that rebuilds BPF maps and swaps them atomically via double-buffer, plus scheduled threat-feed ingestion.
**Target:** A control-plane change reaches active data-plane ≤ 5 s; failed builds keep the previous active slot; feed sync is resilient per source.

### Features

**Agent worker & job pipeline** - IN PROGRESS (spec + context + design + tasks APPROVED)
- Long-running Python worker (`app.worker`, control-plane package, A-AGW-1): blocking-pop consume of `apply:jobs` + startup/periodic **DB-ledger reconcile sweep** (fulfils the M1 outbox promise A-APLY-1/APLY-27/36); all transitions via the executed version-guarded `mark_*` (no new transition logic, APLY-03)
- Handler registry keyed by `JobType` + **applier boundary**: v1 = succeeding placeholder (**D-AGW-1** — `active` = "acknowledged by worker" until M4#2 fills the boundary with the real build/swap); config read from PostgreSQL at apply time (identity-only jobs, A-AGW-5); `JobType` stays `SERVICE_UPDATE`-only (A-AGW-4 — PRD 6.8's `RULE_UPDATE`/`LIST_UPDATE` = `SERVICE_UPDATE`+`trigger`; other types arrive with M4#2/#3, M5)
- Reliability: idempotent by `job_id`/version (duplicate delivery = no-op), no stale-over-new under churn (first concurrent exerciser of the M1 guards), Redis/DB outage = bounded-backoff degrade (Redis down → DB-poll mode); **orphaned-`applying` auto-recovery** on startup via `mark_failed`+existing retry path (**D-AGW-2**, zero new state-machine edges); restart preserves active state; ≤5 s nominal propagation asserted with the v1 applier (A-AGW-7)
- Spec `spec.md` (AGW-01..30); context `context.md` (D-AGW-1..2, A-AGW-1..8); `design.md` (**AD-027**) + 2 rendered diagrams
- Design (AD-027): new `app/worker/` package (`__main__`→`Worker` runtime→loop-free `process_job`/`reconcile_once`/`recover_orphan`→`HANDLERS` registry→`handle_service_update`→injected `Applier`, v1 `PlaceholderApplier`); **crux = two txns/job** (`mark_applying` commits+releases the service FOR UPDATE lock before the applier, terminal mark re-takes it → mid-apply `bump_version→N+1` caught by executed `_superseded`); orphan recovery = 1 txn `mark_failed`+`retry(actor=None)` (system audit already supported); shared `session_scope` UoW added to `db/session.py` (mirrors `get_db` post-commit callbacks so `retry` re-dispatch fires); `Settings` gains `WORKER_*` knobs; **no new models/migration/endpoints**. redis-py async `brpop` return web-verified
- Tasks `tasks.md` (T1–T6, all 30 reqs mapped; baseline **B=209** static, 29 unit + 180 integration): **T1** `session_scope` UoW · **T2** applier boundary+snapshot+`PlaceholderApplier` · **T3** handler registry+`SERVICE_UPDATE` handler · **T4** processor (two-txn guard, reconcile, orphan recovery, supersede-under-churn crux)+truncation fixture · **T5** `Worker` runtime+`WORKER_*` settings+`python -m app.worker` · **T6** docs `[P]`. Only T6 `[P]` (all integration serial per TESTING.md shared infra); `apply.py` byte-for-byte unmodified; no new models/migration/endpoints. Tasks APPROVED (2026-07-10)
- Requires apply-status executed (**satisfied** — M1 landed `a4b1ffd..de47b5f`); pure control-plane, executable independently of M3 fairness Execute; no new endpoints (M1 read surfaces suffice)

**Double-buffer map build/swap** - PLANNED
- Build full inactive slot, verify, then single `active_slot` write; rollback = flip back
- Config maps slotted; runtime-state maps unslotted (8.3)

**Threat intelligence feed sync** - PLANNED
- Fetch/validate/normalize/dedup per source; whitelist-overlap flag + alert (no global removal)
- Bad new feed keeps last-active version; sync stats recorded

---

## M5 — Observability & chargeback

**Goal:** Service-level telemetry aggregation, tenant/admin dashboards, and p95 clean-Gbps metering exported for internal chargeback.
**Target:** Dashboards refresh ≤ 2 s; `BillingUsage` computes `billed_gbps = max(committed, p95_clean)` from exact hot-path byte counts.

### Features

**Telemetry & dashboards** - PLANNED
- `TELEMETRY_AGGREGATE` from counters/events; tenant service view + admin node view
- Drop-reason distribution, top src/dst-port, bloom hit/false-positive, XDP mode, map version, job/feed status

**Chargeback metering** - PLANNED
- p95 clean bps sampling → `BillingUsage` per period; overage policy (`billed`/`capped`)
- Billing bytes from exact per-CPU counters, decoupled from sampled events; export for chargeback

---

## M6 — Operations & SLA

**Goal:** Operational safety and proactive monitoring for real-time DDoS response at Pilot.
**Target:** Global bypass + maintenance mode work with audit/alert; alerting covers data-plane/control-plane/SLA events; per-tenant SLA reports generated.

### Features

**Bypass & maintenance mode** - PLANNED
- Global soft-bypass flag (`active_config`) with "BYPASS ACTIVE" banner + critical alert + audit
- Per-node maintenance mode blocks stray `ACTIVE_SLOT_SWAP`; bypass traffic counted separately; OLA runbook

**Alerting** - PLANNED
- Email + generic webhook; severity + hysteresis + dedup + auto-resolve
- Events: attack onset, `map_error`, XDP native→generic, feed/apply failures, worker/backlog, fairness breach; per-tenant isolation

**SLA/OLA reporting & audit** - PLANNED
- Per-tenant periodic SLA report (met/missed per dimension) tied to `BillingUsage`
- Audit log for service/rule/list/feed/user + dangerous admin actions

---

## M7 — GA track (Future)

**Goal:** Production-readiness beyond the single-node pilot.

### Features

**HA / failover (CM-01, GA Blocker)** - PLANNED — active/passive + link bypass; the condition for an Availability SLA.
**IPv6 forwarding (CM-02)** - PLANNED — high-priority; remove hard-drop.
**Auto-response / one-click mitigate (OP-02)** - PLANNED
**Monitor/count-only rule mode (OP-04)** - PLANNED
**One-click rollback to previous version (OP-05)** - PLANNED

---

## Future Considerations

- Sampled per-tenant drop-flow records for self-service debugging (OP-06)
- Whitelist/blacklist `expires_at` reconciliation sweep (BL-07)
- Stateless SYN-cookie / scan detection to back TCP SYN-flood/port-scan claims (BL-04)
- PII retention/anonymization for `top_src` (CM-08); multi-admin & separation of duties (OP-07)
- Guided onboarding + learning mode (OP-08); SSO/IdP + MFA for admins (CM-10)
