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

**Agent worker & job pipeline** - VERIFIED (executed; 262 control-plane tests passed)
- Long-running Python worker (`app.worker`, control-plane package, A-AGW-1): blocking-pop consume of `apply:jobs` + startup/periodic **DB-ledger reconcile sweep** (fulfils the M1 outbox promise A-APLY-1/APLY-27/36); all transitions via the executed version-guarded `mark_*` (no new transition logic, APLY-03)
- Handler registry keyed by `JobType` + **applier boundary**: v1 = succeeding placeholder (**D-AGW-1** — `active` = "acknowledged by worker" until M4#2 fills the boundary with the real build/swap); config read from PostgreSQL at apply time (identity-only jobs, A-AGW-5); `JobType` stays `SERVICE_UPDATE`-only (A-AGW-4 — PRD 6.8's `RULE_UPDATE`/`LIST_UPDATE` = `SERVICE_UPDATE`+`trigger`; other types arrive with M4#2/#3, M5)
- Reliability: idempotent by `job_id`/version (duplicate delivery = no-op), no stale-over-new under churn (first concurrent exerciser of the M1 guards), Redis/DB outage = bounded-backoff degrade (Redis down → DB-poll mode); **orphaned-`applying` auto-recovery** on startup via `mark_failed`+existing retry path (**D-AGW-2**, zero new state-machine edges); restart preserves active state; ≤5 s nominal propagation asserted with the v1 applier (A-AGW-7)
- Spec `spec.md` (AGW-01..30); context `context.md` (D-AGW-1..2, A-AGW-1..8); `design.md` (**AD-027**) + 2 rendered diagrams
- Design (AD-027): new `app/worker/` package (`__main__`→`Worker` runtime→loop-free `process_job`/`reconcile_once`/`recover_orphan`→`HANDLERS` registry→`handle_service_update`→injected `Applier`, v1 `PlaceholderApplier`); **crux = two txns/job** (`mark_applying` commits+releases the service FOR UPDATE lock before the applier, terminal mark re-takes it → mid-apply `bump_version→N+1` caught by executed `_superseded`); orphan recovery = 1 txn `mark_failed`+`retry(actor=None)` (system audit already supported); shared `session_scope` UoW added to `db/session.py` (mirrors `get_db` post-commit callbacks so `retry` re-dispatch fires); `Settings` gains `WORKER_*` knobs; **no new models/migration/endpoints**. redis-py async `brpop` return web-verified
- Tasks `tasks.md` (T1–T6, all 30 reqs verified): `session_scope`, applier boundary/snapshot,
  handler registry, two-transaction processor/reconcile/orphan recovery, worker runtime/settings, and
  docs all executed; final full gate = **262 passed** (2026-07-10).
- Requires apply-status executed (**satisfied** — M1 landed `a4b1ffd..de47b5f`); pure control-plane, executable independently of M3 fairness Execute; no new endpoints (M1 read surfaces suffice)

**Double-buffer map build/swap** - IN PROGRESS (spec + context + design + tasks drafted)
- Replaces agent-worker's `PlaceholderApplier` with a `DoubleBufferApplier` (impl swapped behind the boundary, not the boundary — D-AGW-1): build full inactive slot from PG → structural read-back verify → single `active_config` flip; rollback = abort before flip (last-good slot stays live)
- **D-DBS-1** write via a **C apply-helper binary** (worker subprocess, reuses loader `seed_*`/inner-map routines); loader now **pins** the ~11 slotted config maps + `active_config` (A-DBS-3) · **D-DBS-2** **full-node rebuild every job** (all active services' service-scoped maps) + **carry-forward** feed-owned global deny maps (M4 #3 owns their content) · **D-DBS-3** verify = **structural read-back** before the flip
- Config maps slotted (rebuilt+swapped); runtime-state maps unslotted, untouched (§8.3); no new `JobType` (full-rebuild-per-`SERVICE_UPDATE` subsumes `MAP_REBUILD`/`ACTIVE_SLOT_SWAP` in v1); re-validates ≤5 s (A-AGW-7) with real builds
- Spec `spec.md` (DBS-01..28); context `context.md` (D-DBS-1..3, A-DBS-1..8); `design.md` (**AD-028**) + 2 rendered diagrams (`diagrams/apply-dataflow.{mmd,svg}` component/data-flow, `diagrams/build-verify-swap.{mmd,svg}` sequence)
- Design (AD-028): `DoubleBufferApplier` (Python, no BPF) loads full-node snapshot from PG → serializes to `apply_snapshot.h` binary wire format → execs new C helper `tools/xdpgw-apply.c` (fresh-inner replacement per outer via `bpf_map_create`+install, feed-map pointer-copy carry-forward, structural `verify_slot`, single `active_config` COMMIT); loader pins the 14 config maps + shared `fair_budget.h`; dpstat gains slot/version; no CP schema/JobType change. 1 fact web-verified (userspace map-in-map inner replace); novel separate-process-install-into-pinned-outer de-risked fail-fast (3-rung fallback)
- Tasks `tasks.md` (T1–T8, all 28 reqs mapped; dp `make test` B=91 baseline): T1 contracts+loader pins+fair-math extract (build) · T2 `xdpgw-apply` scaffold+parser+**fresh-inner de-risk**+golden fixture (92) · T3 build/verify/single-write-flip core+verdict (≥97) · T4 fail-closed rollback+version/idempotency (≥101) · T5 `main()`+privileged smoke+`applybulk` ≤5 s/scale (full) · T6 `[P]` `DoubleBufferApplier`+DI swap+settings+fake-helper integration (CP full) · T7 `[P]` dpstat slot/version (build) · T8 `[P]` docs. C-track T1→T5 serial (shared files+smoke); only T6/T7/T8 `[P]`. All 3 pre-approval checks pass
- Requires agent-worker executed first (**satisfied**); reuses frozen M4 build contracts
  (AD-015/019/021/023/025) + pin pattern (AD-017); D-SLRD-1 loader env seed downgrades to initial-slot
  bootstrap

**Threat intelligence feed sync** - VERIFIED (executed 2026-07-10..13; T1–T15 landed; control-plane full gate **435 passed**, data-plane build/quick/full + global-apply smoke/scale all green)
- Authoritative writer of `source=feed`/`scope=global` `BlacklistEntry` (AD-011 deferral, M4 #2 punt): `ThreatFeedSource` + `/feeds` CRUD + `POST /feeds/{id}/sync`; `JobType.feed_sync` + handler
- Fetch/validate/normalize/dedup **per source** (plain IPv4/CIDR line lists only — D-FEED-2); bad feed **keeps last-active** entries + data-plane version; whitelist-overlap → flag + alert, **no global removal** (AD-003); `FeedSyncRun` stats recorded
- **In-worker due-time scheduler** (D-FEED-3) enqueues `FEED_SYNC` for due enabled sources; manual sync always available
- **Slot-aware global-deny rebuild + atomic swap** reusing M4 #2's `xdpgw-apply` helper (inverse carry-forward: rebuild global-deny, carry service maps) — D-FEED-1
- Spec `spec.md` (FEED-01..40, APPROVED); context `context.md` (D-FEED-1..3, A-FEED-1..8);
  `design.md` + rendered architecture/sequence diagrams (**AD-029, APPROVED**)
- Design: run-linked generalized `AgentJob` lifecycle; bounded background fetch lane; many-to-many feed
  assertions over one materialized global row (manual promotion/demotion); SQL/GiST whitelist overlap;
  desired-vs-active global digest; same `xdpgw-apply` gains global mode with inverse carry-forward and a
  shared helper lock; 32 MiB/30 s fetch bound and 300..604800 s interval range
- Agent-worker prerequisite is satisfied; real propagation remains **Execute hard-gated** on
  double-buffer M4 #2. Reuses AD-023 global-blacklist build contract + `BlacklistEntry`
  (blacklist-filters VERIFIED)
- Tasks `tasks.md` (T1–T15; all 40 requirements mapped): T1 schema/job targets · T2 parser `[P]` ·
  T3 bounded fetcher `[P]` · T4 assertion/overlap reconcile · T5 source lifecycle/enqueue · T6 manual
  precedence · T7 API/history · T8 typed worker lifecycle · T9 sync runner · T10 scheduler · T11
  background fetch coordinator · T12–T14 M4 #2-gated snapshot/Python applier/C global mode · T15 docs.
  Mandatory granularity, dependency-diagram, and test co-location checks all pass; only T2/T3 parallel.

---

## M5 — Observability & chargeback

**Goal:** Service-level telemetry aggregation, tenant/admin dashboards, and p95 clean-Gbps metering exported for internal chargeback.
**Target:** Dashboards refresh ≤ 2 s; `BillingUsage` computes `billed_gbps = max(committed, p95_clean)` from exact hot-path byte counts.

### Features

**Telemetry & dashboards** - P1 VERIFIED (2026-07-13; P2/P3 deferred)
- End-to-end vertical slice: new **exact per-CPU per-service counters** on the XDP hot path (clean + per-reason drop pkts/bytes — D-TEL-1, clean-byte = billing source reused by chargeback) → worker background telemetry lane (not a `JobType`; in-worker 1–2 second scheduler) rolling windowed `TelemetryCounter` rows → telemetry/health API → **first React SPA** (shell + telemetry/node views only, D-TEL-2)
- Tenant per-service view (clean-vs-drop, drop-reason distribution, PPS/BPS) + admin node view (XDP mode, map version, apply status, `map_error`, backlog, feed status, throughput-vs-capacity); **REST polling ≤2 s** (D-TEL-3); sampled top dst-port + top src IP (D-TEL-4, `top_src` PII pilot-accepted CM-08); strict tenant isolation (5.2)
- Spec `spec.md` (TEL-01..40; P1=01..30 MVP slice, P2=31..38, P3=39..40); context `context.md` (D-TEL-1..4, A-TEL-1..8)
- Out of scope: p95/`BillingUsage` (sibling *Chargeback metering*), alert firing (M6), bypass/maintenance banner (M6), config CRUD screens (separate frontend effort), SSE/WebSocket, added-latency p99
- Reuses executed data-plane counter/pin patterns (AD-017) + worker `HANDLERS`/`Applier` boundary (AD-027) + C-helper subprocess pattern
- Design `design.md` (**AD-030**) + 2 rendered diagrams (`diagrams/telemetry-architecture.{mmd,svg}` data-flow, `diagrams/aggregation-tick.{mmd,svg}` sequence). Key decisions: **D-030-1** `svc_stat_map` = prealloc `PERCPU_HASH[dp_id]` (288 B value, `drop_by_reason[DROP_REASON_CAP]` for ABI-stable width) counted at the `redirect_out`/`record_drop` choke points; **D-030-2** `pkt_meta.frame_len` (repurpose pad, stays 40 B) for byte counts; **D-030-4** new `ProtectedService.dp_id` (monotonic seq, ≥1) is the u32↔UUID surrogate written into `service_val.service_id`, including the M4 #2 applier; **D-030-5** reader = extend `dpstat` with `snapshot --json` (reuses `open_pinned_map`/`read_percpu_u64`); **D-030-6** aggregation = worker **background asyncio task** like the feed lane (NOT a Redis `JobType` — supersedes A-TEL-2, no ledger writes at 2 s); **D-030-7** node health = DP-derived fields persisted (`NodeHealthSnapshot`) + live `AgentJob`/`FeedSyncRun` reads; API reuses `load_service_for_principal` (404 cross-tenant) + `require_admin` + `/auth/me`; SPA = Vite+React+TS+TanStack Query (`refetchInterval` 2 s)+Recharts. New models `TelemetryCounter`+`NodeHealthSnapshot` (migration `20260710_0007`).
- P1 status: T1–T12 and T17 are complete. Final validation: CP full gate
  **450 passed** (18 existing Pydantic deprecation warnings); frontend gate
  **9 files / 13 tests** plus production build; data-plane build/quick
  **130 passed** and privileged redirect/fairness/apply smoke; browser
  deep-link/login/404, tenant isolation, two-second polling, and critical-XDP
  validation passed against FastAPI-served assets.
  `CONTROL_PLANE_FRONTEND_STATIC_DIR` is opt-in and has an HTML-only history
  fallback that preserves API and asset 404s. TEL-01–30 are verified. P2
  T13–T15 and P3 T16 remain explicitly deferred; TEL-31–40 remain pending.

**Chargeback metering** - IN PROGRESS (spec + context + design APPROVED + tasks drafted)
- p95 clean bps sampling → `BillingUsage` per period; overage policy (`billed`/`capped`)
- Billing bytes from exact per-CPU counters, decoupled from sampled events; export for chargeback
- Spec `spec.md` (CHG-01..33; P1=01..25 MVP, P2=26..31, P3=32..33); context `context.md` (D-CHG-1..4, A-CHG-1..8)
- 4 gray areas resolved (AskUserQuestion): **D-CHG-1** dedicated 5-min `BillingSample` series (decoupled from telemetry 2 s windows) · **D-CHG-2** worker sampler + scheduled period-close rollup → immutable `BillingUsage` (`open`/`final`, running current-period estimate; background lane, no Redis `JobType`) · **D-CHG-3** UTC calendar-month periods, no proration · **D-CHG-4** per-service (1:1 `ServicePlan`), VIP/whitelist clean **included**, bypass **excluded** (M6)
- Reuses D-TEL-1 exact per-service `clean_bytes` counter + telemetry reader/worker-lane patterns; new `BillingSample`+`BillingUsage` models + `/billing/usage` API + export; `ServicePlan.committed_clean_gbps`/`billing_metric`/`overage_policy` reused verbatim; `billed_gbps = max(committed, p95)` (TDD §4.8)
- **Execute-gated on *Telemetry & dashboards* executed** (clean-byte counter + `dp_id` contract); CP billing model/API/rollup buildable ahead against a fake reader (plan-ahead artifact, like telemetry vs M4)
- Design `design.md` (**AD-031**) + 2 rendered diagrams (`diagrams/billing-architecture.{mmd,svg}` data-flow, `diagrams/metering-cycle.{mmd,svg}` sequence). **Control-plane-only, zero new DP surface.** Key decisions: **D-031-1** reuse telemetry's `clean_bytes` counter + `dpstat`/`TelemetryReader`, sampler keeps its own `prev[dp_id]`; **D-031-2** nearest-rank p95 (pure Python, no numpy) + bytes/sec→Gbps **×8/1e9** (the load-bearing unit fix vs gigabit `committed_clean_gbps`); **D-031-3** one worker background lane `BillingMeter.run_loop` (sample+rollup+prune/tick, 5-min, no Redis `JobType`); **D-031-4** `BillingUsage` open→final immutable, `UNIQUE(service_id, period_start)`; **D-031-5** deletion = `SET NULL` + finalize NULL-service rows (no reach into M1 `delete_service`); **D-031-6** UTC calendar-month `billing_period.py`; **D-031-7** `BillingSample` CASCADE (transient) vs `BillingUsage` SET NULL + `tenant_id`/`service_name` snapshots (durable); **D-031-8** `/billing` router reuses session/RBAC + `load_service_for_principal` (404), tenant via denormalized `tenant_id`, admin all + export. New models `BillingSample`+`BillingUsage`(+`BillingStatus` enum), migration `..._0009_billing` (after telemetry head); new settings `worker_billing_*` (interval 300 s, retention 400 d), reuses telemetry reader knobs. 7 flags for Tasks (reader sharing, p95 convention, retention horizon, `tenant_id` FK, cadence coupling, export CSV columns, `period` param shape). All 33 reqs mapped; single CP/worker track (no DP work), SPA panel gated on telemetry FE
- Design **APPROVED** + tasks `tasks.md` drafted (T1–T10, all 33 reqs mapped; baseline `B_cp` pinned live at Execute ≥262+feed+telemetry): **T1** `billing_period.py` UTC-month (unit `[P]`) · **T2** `billing_metrics.py` nearest-rank p95 + bytes→Gbps ×8 (unit `[P]`) · **T3** `BillingSample`+`BillingUsage`+`BillingStatus` models+migration `..._0009` (integration) · **T4** `BillingMeter.sample_once` delta/reset/upsert (integration) · **T5** rollup p95→open+finalize (integration) · **T6** prune+`run_loop`+worker wiring+`worker_billing_*` (integration) · **T7** `/billing` usage+export router (integration) · **T8** history endpoint P3 (integration) · **T9** SPA showback panel P2 (fe gate, gated on telemetry FE) · **T10** docs `[P]`. Only T1/T2 (unit) + T10 (docs) parallel; T3–T8 serialize on `compose.test.yml`. All 3 pre-approval checks pass (granularity, diagram↔deps, test co-location). Tools: `coding-guidelines` code, `docs-writer` T10, no MCPs. Next: **Execute** (hard-gated on telemetry executed; CP slice buildable vs `FakeTelemetryReader`)

---

## M6 — Operations & SLA

**Goal:** Operational safety and proactive monitoring for real-time DDoS response at Pilot.
**Target:** Global bypass + maintenance mode work with audit/alert; alerting covers data-plane/control-plane/SLA events; per-tenant SLA reports generated.

### Features

**Bypass & maintenance mode** - IN PROGRESS (spec + context drafted, awaiting approval)
- Global soft-bypass flag (`active_config`) with "BYPASS ACTIVE" banner + critical alert + audit
- Per-node maintenance mode blocks stray `ACTIVE_SLOT_SWAP`; bypass traffic counted separately; OLA runbook
- Spec `spec.md` (BYP-01..33; P1=01..27 MVP, P2=28..30 banner, P3=31..33 runbook/history); context `context.md` (D-BYP-1..4, A-BYP-1..8)
- 4 gray areas resolved (AskUserQuestion): **D-BYP-1** bypass scope = parsed **IPv4 + ARP only** (short-circuit after parse, skip verdict pipeline; IPv6/malformed/fragment still fail-fast drop) · **D-BYP-2** maintenance = **queue-and-apply-on-exit** (worker builds inactive slot, holds the flip, mutations still queue 202, latest good config swaps on exit) · **D-BYP-3** toggle path = **immediate control channel** (DB desired-state audited + fast worker reconcile lane jumps the service-apply backlog, ~1 tick, survives restart via re-assert; no `NODE_CONTROL` JobType) · **D-BYP-4** bypass accounting = **add node-global exact per-CPU bypass counter now** (separate from `svc_stat` clean; dpstat + `/node/health`; chargeback exclusion = A-CHG-8, M5)
- Owns: `NodeControl` desired-state model + `/node/bypass`|`/node/maintenance`|`/node/health` API; worker node-control reconcile lane (assert bypass flag + gate the M4 #2 flip for maintenance); hot-path bypass short-circuit; node-global bypass counter; audit + alert-worthy event per toggle; OLA runbook
- Reuses M1 `require_admin`/`AuditEvent`; M4 #1 worker + background-lane pattern + `Applier` boundary; M4 #2 `active_config` write path + `xdpgw-apply` + pins; M2 `redirect_out`/`tx_devmap` + `dpstat`; M5 `svc_stat` (bypass counted separately) + SPA shell (banner P2)
- **Execute-gated on M4 #2 executed** for the real DP effects (hot-path pass-through, bypass counter, maintenance swap-hold); CP control surface + audit + state + reconcile-lane logic buildable ahead on the executed M4 #1 worker (feed-sync DP-gate precedent). Plan-ahead M6 artifact — current execution front is M4
- Out of scope: alert **delivery** (sibling *Alerting*), device-level **link bypass** (M7/OP-03), chargeback **exclusion** (M5), SLA reporting (sibling), rollback UI (M7)

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
