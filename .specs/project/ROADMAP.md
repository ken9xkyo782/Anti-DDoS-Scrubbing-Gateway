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
- `AllowRule` (≤16, unique priority, first-match warn), whitelist/VIP + global blacklist CRUD (service-scoped blacklist superseded by feature `service-blacklist-removal`); list sources are arbitrary IPv4 (not source-in-allocation)
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

**Static next-hop L2 (MAC) rewrite from a BPF map** - SPEC + CONTEXT + DESIGN (AD-035) + TASKS DRAFTED (awaiting approval → Execute)
- **Supersedes the mechanism of** the *Service lookup & transparent redirect* amendment SLRD-27..29 / AD-DP-01: replaces the packet-time `bpf_fib_lookup` L2 rewrite (with verbatim fallback) with a **static, pre-resolved BPF-map lookup** — the hot path does a pure lookup + `memcpy`, **no kernel FIB call, no verbatim fallback**
- Control-plane/agent resolves each **single-IP** service's backend MAC out of band via **active ARP probes** (immediate at declare + 30-min refresh) and stores `{dst_mac (per-service), src_mac (node-global OUT MAC)}` in a **pinned, unslotted runtime** next-hop map keyed by `dp_id`; unresolved → **fail-closed drop** (`DR_NEXTHOP_UNRESOLVED`, appended to the frozen §9.2 ABI at index 16)
- `ProtectedService` destination constrained to a **single IPv4 host** (was `cidr_or_ip` CIDR); DP already looks up at `/32`
- Spec `spec.md` (NHR-01..21; P1=01..16 MVP, P2=17..19, P3=20..21); context `context.md` (D-NHR-1..4). 4 gray areas resolved via AskUserQuestion (fail-closed / service-IP-direct + node-global OUT MAC / active ARP / immediate-resolve-at-apply)
- Spans DP (hot-path + new map + drop-reason ABI append + loader pin + `dpstat` next-hop surface) + CP (single-IP constraint) + a new worker **ARP-probe resolution lane** (writer = `dpstat`-family subcommand, mirrors M6 `dpstat set-bypass` / `NodeControlReconciler`); reuses M2 `redirect_out`/`tx_devmap`, M4 #1 worker background-lane pattern, M6 privileged-`dpstat`-writer pattern
- Design `design.md` (**AD-035**) + 2 rendered diagrams (`diagrams/nexthop-architecture.{mmd,svg}`, `diagrams/resolve-and-rewrite-sequence.{mmd,svg}`). 2 flagged items decided: **D-035-B** bypass = **verbatim** (no per-service context; keeps `bpf_fib_lookup` fully removed), **D-035-M** existing non-`/32` = validate + report, no auto-convert. Key: `nexthop_map` `HASH`+`NO_PREALLOC` unslotted keyed by `dp_id` (web-verified RCU-atomic tear-free); OUT src MAC read live from `tx_devmap[0]` (no new seed map); ARP probe+write in the C `dpstat` tool (web-verified `AF_PACKET`/`ETH_P_ARP`), worker execs unprivileged; new `NextHopResolver` lane; `redirect_out` reordered resolve-first→fail-closed; **zero new DB table**. All 21 reqs mapped to components
- Tasks `tasks.md` (DT1–DT3 ∥ CT1–CT6; all 21 reqs mapped): **DP** DT1 `nexthop.h`+ABI+hot-path fail-closed+bypass-verbatim+dp-unit (quick) · DT2 loader pin+dpstat resolve/evict/set-nexthop+snapshot (build) · DT3 live veth smoke (dp-integration) · **CP** CT1 single-IPv4-host validation+report · CT2 `NextHopResolver` lane+writers · CT3 worker wiring+post-apply hook · CT4 (P2) `/node/health` unresolved count · CT5 (P3) manual resolve+metrics · CT6 `[P]` docs. DP∥CP concurrent (CP tests use fakes → no hard cross-track gate); only CT6 `[P]` (integration not parallel-safe). All 3 pre-approval checks pass. Baselines pinned live at Execute (`B_dp`≥130, `B_cp` head). Tools: `coding-guidelines` code, `docs-writer` CT6, no MCPs
- **Next:** approve tasks → **Execute** (Phase 1: DT1 ∥ CT1)

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
- 2-tier committed (*(amended by C1: per-CPU `PERCPU_HASH`)*) / burst (per-CPU, `ceiling−committed`) buckets per service at the **ARL-24 seam** (`admit_clean()`); burst dual-draws the node headroom bucket (`capacity−Σcommitted`, floor 0); drops `service_ceiling_drop`/`congestion_drop`; VIP never enters the ladder (§8.4.6 structural)
- Ingress-cost cap at **WLV-24 seam A** (pre-whitelist, destination-keyed spoof-immune): dual bps + derived-pps budget = `k×ceiling`, **k=3** default, ref packet size ~512 B node-tunable (**D-FAIR-1**); over-cap = early `ingress_cap_drop`; VIP traffic subject to the cap (documented precedence)
- Wires the last 3 frozen ABI indices **11/12/13** — all 16 §9.2 reasons live; per-service rates via a **new** slotted config map from `ServicePlan` (M4 build contract, A-FAIR-2); 3 runtime maps unslotted; `node_clean_capacity` = env-driven seed, 40 Gbps §15 default when unset (**D-FAIR-2**)
- Deterministic fairness scenario = the **M3 milestone gate** (flood A → B's committed admits 100%, FAIR-24); spin-lock-in-XDP de-risked fail-fast with fallback (FAIR-22); default seed keeps post-BLK baseline verdict-identical
- Spec `spec.md` (FAIR-01..27); context `context.md` (D-FAIR-1..2, A-FAIR-1..8 — AD-024); `design.md` + rendered diagrams (ladder flow + map layout)
- Design (AD-025, amended by C1/AD-042): new `src/fairness.h` (both stages + 2 slotted config maps + 4 runtime bucket maps); committed = `PERCPU_HASH` of `struct rl_bucket` (lock-free per-CPU split, scaling to 148 Mpps at 96 cores); burst/node/cap reuse `rl_bucket`/helpers verbatim; budgets precomputed userspace (k/ref-pkt/capacity = env only); `pkt_meta` first deliberate growth 32→40 (`fair_state`); `FAIR_RATE_MAX` 16e9 B/s overflow clamp.
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

**Telemetry & dashboards** - VERIFIED (P1 2026-07-13; P2/P3 2026-07-14) — all 40 TEL reqs verified
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
  fallback that preserves API and asset 404s. TEL-01–30 are verified.
- P2/P3 status (2026-07-14): T13–T16 complete → **all 40 TEL reqs verified**.
  T13 sampled top-talkers; T14 richer node-health metrics (full CP gate re-run
  green after billing landed); T15 P2 SPA panels (`TopTalkersPanel`,
  `BloomFpPanel`, `CommittedHonoredPanel`, `FeedStatusPanel`) + `theme/thresholds.ts`
  §9.1 display coloring; T16 telemetry history/export endpoints + `TrendChart`.
  Final gates: CP `pytest -q` **507 passed**; FE **17 files / 34 tests** + build.

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

**Bypass & maintenance mode** - IN PROGRESS (spec + context + design + tasks drafted; awaiting approval → Execute)
- Global soft-bypass flag (`active_config`) with "BYPASS ACTIVE" banner + critical alert + audit
- Per-node maintenance mode blocks stray `ACTIVE_SLOT_SWAP`; bypass traffic counted separately; OLA runbook
- Spec `spec.md` (BYP-01..33; P1=01..27 MVP, P2=28..30 banner, P3=31..33 runbook/history); context `context.md` (D-BYP-1..4, A-BYP-1..8)
- 4 gray areas resolved (AskUserQuestion): **D-BYP-1** bypass scope = parsed **IPv4 + ARP only** (short-circuit after parse, skip verdict pipeline; IPv6/malformed/fragment still fail-fast drop) · **D-BYP-2** maintenance = **queue-and-apply-on-exit** (worker builds inactive slot, holds the flip, mutations still queue 202, latest good config swaps on exit) · **D-BYP-3** toggle path = **immediate control channel** (DB desired-state audited + fast worker reconcile lane jumps the service-apply backlog, ~1 tick, survives restart via re-assert; no `NODE_CONTROL` JobType) · **D-BYP-4** bypass accounting = **add node-global exact per-CPU bypass counter now** (separate from `svc_stat` clean; dpstat + `/node/health`; chargeback exclusion = A-CHG-8, M5)
- Owns: `NodeControl` desired-state model + `/node/bypass`|`/node/maintenance`|`/node/health` API; worker node-control reconcile lane (assert bypass flag + gate the M4 #2 flip for maintenance); hot-path bypass short-circuit; node-global bypass counter; audit + alert-worthy event per toggle; OLA runbook
- Reuses M1 `require_admin`/`AuditEvent`; M4 #1 worker + background-lane pattern + `Applier` boundary; M4 #2 `active_config` write path + `xdpgw-apply` + pins; M2 `redirect_out`/`tx_devmap` + `dpstat`; M5 `svc_stat` (bypass counted separately) + SPA shell (banner P2)
- Out of scope: alert **delivery** (sibling *Alerting*), device-level **link bypass** (M7/OP-03), chargeback **exclusion** (M5), SLA reporting (sibling), rollback UI (M7)
- Design `design.md` (**AD-032**) + 2 rendered diagrams (`diagrams/bypass-architecture.{mmd,svg}` component, `diagrams/bypass-sequence.{mmd,svg}` toggle-propagation + maintenance hold/drain). **Gating correction:** because **D-032-1** stores the bypass indicator in a **dedicated `node_control` BPF `ARRAY[1]` map** (not a field in `active_config`), the whole feature is **buildable on the current executed base** (M1+M2+M4 #1+in-progress M5) — **NOT hard-gated on M4 #2** (supersedes the spec's assumption + removes A-BYP-3's field-preserving contract on the applier). Key decisions: **D-032-2** hot-path short-circuit = post-parse `if (node_control_bypass()) redirect_out_bypass()` before `service_lookup_redirect` (ARP unchanged; IPv6/malformed/fragment still drop) · **D-032-3** `bypass_counter` `PERCPU_ARRAY[1]` outside frozen `counter_map` (like `bloom_stats`), `svc_stat` untouched · **D-032-4** maintenance = worker **apply-dispatch gate** (`_maintenance_active` before `mark_applying` → jobs stay `queued`, drain on clear; applier stays maintenance-agnostic; works today with `PlaceholderApplier`, the real slot-swap it defers arrives with M4 #2) · **D-032-5** propagation = new `NodeControlReconciler` background asyncio lane (separate task, 1 s tick, execs `dpstat set-bypass` on drift, re-asserts on restart; no `NODE_CONTROL` JobType) · **D-032-6** DP writer = extend **`dpstat`** with a `set-bypass 0|1` subcommand (not the M4 #2 `xdpgw-apply` binary) · **D-032-7** `NodeControl` = **singleton** table (`CheckConstraint id=1`) desired-state · **D-032-8** `/node/health` reuses **`TelemetryReader.snapshot()`** extended with a bypass block (desired ⊕ effective); alerts = `AuditEvent` + health state for M6 *Alerting* (no dedicated alert table). New DP: `src/node_control.h` (+`node_control`/`bypass_counter` maps, loader pins+seed, dpstat `set-bypass`/snapshot bypass block); new CP: `NodeControl` model + migration, `services/node_control.py`, `api/routers/node.py` (`/node/bypass`|`/node/maintenance`|`/node/health`), `worker/node_control_reconciler.py`, processor maintenance gate, `worker_node_control_*` settings; P2 SPA banner on the M5 shell. **Soft coordination (not hard gates):** M4 #2 applier (what maintenance defers), M5 telemetry `/node/health`+`TelemetryReader` (extend not duplicate), M5 chargeback (A-CHG-8 reads the counter), M5 SPA shell (banner). 6 flags for Tasks (node-health router ownership; dpstat set-bypass privilege; singleton idiom; maintenance gate FEED_SYNC scope; DP-unit bypass seam via real `node_control` map; migration down_revision pinned live). Plan-ahead M6 artifact — current execution front is M4/M5
- Tasks `tasks.md` (T1–T11; all 33 reqs mapped; 3 tracks DP∥CP∥FE): **DP** T1 `node_control.h`+hot-path branch+dp-unit (quick) · T2 loader pins/seed+dpstat `set-bypass`/snapshot bypass (build) · T3 live veth bypass smoke (dp-integration) · **CP** T4 `NodeControl` singleton model+migration · T5 `services/node_control.py` toggles+audit · T6 `TelemetryReader` bypass ext (**unit `[P]`**) · T7 `/node` router (bypass/maintenance/health)+schemas · T8 maintenance apply-dispatch gate (processor) · T9 `NodeControlReconciler` lane+`DpstatBypassWriter`+settings+worker spawn · **FE** T10 SPA banner (P2, fe gate, gated on M5 shell) `[P]` · T11 docs+OLA runbook `[P]`. Serial within CP (shared `compose.test.yml`): T4→T5→T8→T7→T9; only T6/T10/T11 `[P]`; DP T1→T2→T3 serial. All 3 pre-approval checks pass (granularity — T2/T7/T9 flagged cohesive-not-split; diagram↔`Depends on`; test co-location — T2 loader/dpstat build-gated, covered by the privileged T3 smoke per the DP-established pattern). Tools: `coding-guidelines` code, `docs-writer` T11, no MCPs. Baselines pinned live at Execute (B_dp≥91+telemetry, B_cp≥262+feed+telemetry, B_fe). Next: **approve tasks → Execute** (Phase 1: T1+T4 track leads, T6 `[P]`)

**Alerting** - IN PROGRESS (spec + context + design + tasks drafted; awaiting approval → Execute)
- Worker-side `AlertEvaluator` lane turns the mandated §9.3 events into **email + generic-webhook**
  notifications; ≥3 severities (info/warning/critical); **for-duration + hysteresis + dedup + auto-resolve**;
  per-tenant isolation (service→owning tenant+admin, node→admin only); critical→audit; queryable history —
  **zero hot-path change** (reads existing telemetry/health/job/feed/node-control/audit sources, no new DP surface/counter)
- Events wired: attack onset, `map_error`, XDP native→generic, near-capacity, apply `failed`, worker/backlog,
  feed-sync fail, committed/fairness breach, bloom-FP, bypass/maintenance active, whitelist-overlaps-feed
- Spec `spec.md` (ALRT-01..42; P1=01..34 MVP, P2=35..39, P3=40..42); context `context.md` (D-ALRT-1..4, A-ALRT-1..8)
- 4 gray areas resolved (AskUserQuestion): **D-ALRT-1** new dedicated worker lane (counter-derived alerts
  exist, not event-only) · **D-ALRT-2** stateful `AlertRule`/`Alert`/`AlertNotification`/`NotificationChannel`
  lifecycle tables (supersedes bypass-maintenance's "no alert table" note) · **D-ALRT-3** admin-global
  channels + §9.1-seeded tunable thresholds + ownership routing · **D-ALRT-4** for-duration + hysteresis-band
  + dedup + re-notify window + auto-resolve
- Execute-gated on **Telemetry & dashboards executed** (satisfied — VERIFIED 2026-07-14) + agent-worker
  (satisfied); bypass-maintenance (M6 #1) = **soft coordination** (its `NodeControl` table already exists at
  migration `_0010`, so ALRT-19 fires today; ALRT-09 fail-safe skips any absent source)
- Design `design.md` (**AD-033**) + rendered diagrams (`diagrams/alerting-architecture.{mmd,svg}` component,
  `diagrams/alerting-sequence.{mmd,svg}` evaluate→debounce→dispatch→auto-resolve). **Control-plane/worker
  only, zero DP surface.** Key decisions: **D-033-1** rules bind to structured source rows (not audit-log
  parsing; `AuditEvent` = critical-alert *write* target) · **D-033-2** routing/isolation keyed by
  `NotificationChannel.tenant_id` (no email column exists on User/Tenant — node→NULL-scope channels,
  service→owner+NULL; §5.2 structural) · **D-033-3** single `Alert` row per `(rule,scope)` walking
  `pending→firing→resolved` with all streak/notify state in-row (restart-safe) + partial-unique dedup index ·
  **D-033-4** new `AlertEvaluator` worker lane, 15 s tick, not a Redis `JobType` · **D-033-5** webhook=httpx
  (existing dep), email=stdlib `smtplib`/`to_thread` (no new dep) · **D-033-6** thresholds mirror
  `theme/thresholds.ts` §9.1 + per-rule admin override · **D-033-7** in-worker lane detects
  backlog/stuck/telemetry-staleness; full worker-death = documented external-monitor gap · **D-033-8** 4
  tables (`alert_rule`/`notification_channel`/`alert`/`alert_notification`) + migration `_0011` (after
  `_0010`), SET-NULL durable history. Extracts `_committed_clean_bps`→`services/telemetry_math.py` (import
  refactor). 8 flags for Tasks. All 42 reqs mapped.
- Tasks `tasks.md` (T1–T12; all 42 reqs mapped): **T1** extract `telemetry_math` committed-bps helper ·
  **T2** `[P]` `alert_rules` §9.3 catalog + pure predicates · **T3** 4 models + 5 enums + migration `_0011` ·
  **T4** `AlertSources`→`AlertInputs` · **T5** `AlertEvaluator` lifecycle (debounce/hysteresis/dedup/
  auto-resolve + run_loop + critical→audit + maintenance silence) · **T6** dispatcher + email/webhook
  channels + scoped routing/isolation + test-send · **T7** worker wiring + `worker_alert_*` settings +
  retention prune · **T8** `/alerts` history read (tenant-scoped) · **T9** `/alerts/rules`+`/alerts/channels`
  admin config + test-send · **T10** SPA panel (P2, fe, gated telemetry FE) · **T11** P3 ack + export ·
  **T12** `[P]` docs. Single CP/worker track, **zero DP work**; only **T2** (unit) + **T12** (docs) + **T10**
  (fe toolchain) are `[P]`, all else integration→serial on `compose.test.yml`. All 3 pre-approval checks pass
  (granularity, diagram↔deps, test co-location). Tools: `coding-guidelines` code, `docs-writer` T12, no MCPs.
  Baselines pinned live at Execute (`B_cp`≥507, `B_fe`≥34). Next: **approve tasks → Execute**

**SLA/OLA reporting & audit** - IN PROGRESS (spec + context drafted; awaiting approval → Design)
- Worker-side lane materializes a per-tenant, per-period **SLA report** (met/missed per dimension, open→final
  immutable, tied to the `BillingUsage` UTC-month period) + an admin **OLA** operational summary — reads only
  already-persisted evidence, **zero hot-path change, no new DP surface/counter, no fabricated numbers**
- Dimensions (measurable-only): committed-clean-bandwidth honored (from persisted fairness-breach `Alert`
  durations), billing/chargeback (`BillingUsage`), + OLA apply-reliability (`AgentJob failed`)/feed-health
  (`FeedSyncRun`)/bypass-maintenance windows (`AuditEvent`+`NodeControl`); Availability + added-latency-p99
  disclosed **best-effort, not scored** (AD-007 / A-TEL-8); absent source → `insufficient_data`
- Audit **read/query/export** surface (admin-only) over the **already-complete** write path (41 `record_event`
  sites; `AuditEvent` has no `tenant_id` → admin-only, tenant self-audit deferred); no write-path change
- Spec `spec.md` (SLA-01..36; P1=01..27 MVP, P2=28..33, P3=34..36); context `context.md` (D-SLA-1..4, A-SLA-1..8)
- 4 gray areas resolved (AskUserQuestion): **D-SLA-1** worker lane → immutable `open`→`final` period rows ·
  **D-SLA-2** measurable-only dimensions from persisted evidence (best-effort exclusions never fabricated) ·
  **D-SLA-3** admin-only audit read/query/export, no write-path change · **D-SLA-4** API + export + P2 SPA
  panel, SLA (tenant) vs OLA (admin) split, no auto-email in v1
- **Execute readiness:** all evidence sources present in-tree (`BillingUsage`/`_0009`, `NodeControl`/`_0010`,
  Alerting models/`_0011`) → **no hard external gate**; committed-honored soft-coordinates on *Alerting*'s
  fairness-breach `Alert` rows (degrades to `insufficient_data` until present). New models + migration `_0012`
  (after `_0011_alerting`). Reuses M1 RBAC/`AuditEvent`/`scrub_metadata`, `billing_period.py` UTC-month,
  billing/telemetry export+retention patterns, M5 SPA shell (P2)

---

## Frontend / operability (cross-cutting, no backend milestone gate)

**Goal:** Make the Pilot fully operable from the SPA — configuration management, not just observability. Surfaces the already-shipped M1/M4/M6 APIs; pure frontend.

### Features

**Configuration management SPA (admin & tenant)** - SPEC + DESIGN + TASKS DRAFTED (AD-034; awaiting approval → Execute)
- Closes the deferred *"Config CRUD screens in the SPA"* effort (telemetry-dashboards `D-TEL-2` punt) on top of the existing, tested APIs; **pure frontend, zero new backend endpoint/model/migration**
- Tenant self-service (P1): services, allow-rules, whitelist/VIP, service blacklist — all ownership-guarded (`load_service_for_principal`); every mutation surfaces the async apply lifecycle (`pending→queued→applying→active|failed`) + `version`/`active_version`, never implying instant application
- Admin console (P2): tenants/users, CIDR allocation, service oversight + **admin-only** plan sizing (`PATCH /services/{id}/plan`), threat feeds + global blacklist, alert rules/channels + test-send, node bypass/maintenance
- Account change-password + admin apply/job backlog (P3)
- Spec `spec.md` (CFG-01..53; P1=01..24 MVP, P2=25..50, P3=51..53). **No backend milestone gate** — P1 buildable today; P2 alerting/node-control stories **soft-depend** on M6 *Alerting* + *Bypass & maintenance* executed (their config/control endpoints must exist)
- Design `design.md` (**AD-034**) + 2 rendered diagrams (`diagrams/config-architecture.{mmd,svg}`, `diagrams/apply-status-ux.{mmd,svg}`). **UI/UX directive:** the SPA has **zero CSS today** → the design introduces a real design system. Key decisions: **D-034-1** unified **`radix-ui`** (React 19-verified) + **CSS Modules** + **CSS-variable tokens** (light+dark) · **D-034-2** rebuild the shell (Sidebar+Topbar) app-wide, rehome+base-restyle existing observability panels (visual-only, tests stay green) · **D-034-3** enhance `apiClient` to parse FastAPI `{detail}` → inline field errors · **D-034-4** async-apply UX = `useApplyStatus` (1 s poll while non-terminal, 30 s soft-timeout) + StatusBadge + toasts + Topbar indicator · **D-034-5** dep-free forms (no RHF/zod) · **D-034-6** Sidebar IA (Overview/Manage[role]/Observe) + service-detail Radix Tabs · **D-034-7** tenant plan display-only, admin plan-at-create · **D-034-8** zero backend change (missing endpoint → SPEC_DEVIATION) · **D-034-9** dual-theme tokens · **D-034-10** Vitest primitives+screens, keep existing tests green. All 53 reqs mapped to components
- Tasks `tasks.md` (T1–T19; all 53 reqs mapped; single **frontend** track, all **fe-unit**/**fe** gate): **T1** tokens+base+theme+`radix-ui` dep · **T2/T3/T4** primitive families (form / overlay-nav / data-display) · **T5** `[P]` apiClient `{detail}` parse + DTO types · **T6** `useApplyStatus`+indicator · **T7** app-shell rebuild (Sidebar/Topbar/role-routing) · **T8** `[P]` tenant resource hooks · **T9→T10→T11** tenant services/rules/lists (P1 demoable slice) · **T12/T13/T14/T15/T16/T17** `[P]` admin console pages (tenants+users, allocations, services+plan, feeds+global-BL, alerting, node) · **T18** `[P]` account+job-backlog (P3) · **T19** `[P]` docs. All 3 pre-approval checks pass (granularity — T2/T3/T4/T7 flagged cohesive-not-split; diagram↔`Depends on`; test co-location = fe-unit everywhere). Endpoints for P2 (`/alerts/*`, `/node/*`) verified present in-tree → admin screens buildable now. `B_fe`=Vitest head total (≥34) pinned live at Execute. Tools: `coding-guidelines` (code), `docs-writer` (T19), no MCPs
- Reuses the M5 SPA (Vite+React 19+TS, TanStack Query, `AuthContext`/`ProtectedRoute`, `apiClient`, `theme/thresholds.ts` severity colors); `AppLayout`→`AppShell`; extends, does not fork

**UDP amplification config & DDoS Protection tab** - VERIFIED (P1 executed 2026-07-22; AMP-01..19; P2 AMP-20..21 deferred)
- Realizes the **deferred D-BLK-2 / AD-022 item**: the control-plane writer for the dynamic `udp_blocked_port_bitmap` that *Blacklist & deny filters* shipped with **enforcement only** (v1 writer = loader env seed). New **DDoS Protection** admin tab (named broader than v1 scope for future deny-filter controls); v1 content = UDP amplification management
- **Not pure-frontend** — spans CP (new admin model + API + audit), worker (a new reconcile lane), DP (`dpstat set-blocked-ports` subcommand), and FE (the tab). **Zero hot-path change, zero new drop reason, zero apply-snapshot wire change, no new `JobType`**
- Spec `spec.md` (AMP-01..21; P1=01..19 MVP, P2=20..21 effective-state read-back); context `context.md` (D-AMP-1..3, A-AMP-1..8)
- 3 gray areas resolved (AskUserQuestion): **D-AMP-1** scope = dynamic port-list CRUD + read-only built-in set (no node-wide toggle, no per-built-in override — both deferred) · **D-AMP-2** propagation = **worker reconcile lane + `dpstat set-blocked-ports`** (mirrors `NodeControlReconciler`/`NextHopResolver`, present in code) — **not** an apply-snapshot wire-format extension (avoids stacking on the in-flight service-ratelimit v3 bump) · **D-AMP-3** entry model = single UDP port (0..65535) + optional note, node-global, admin-only, no expiry/ranges
- **Depends on (all executed / in-tree):** blacklist-filters (enforcement + slotted `udp_blocked_port_bitmap`, ABI idx 7, VERIFIED); double-buffer-swap (`xdpgw-apply` carries the bitmap forward — writer must be carry-forward-safe); agent-worker (runtime + background-lane pattern); bypass/next-hop (the `DpstatBypassWriter`/`NodeControlReconciler` + `dpstat set-bypass`/`set-nexthop` template to clone); config-management-spa (admin shell, role-filtered Sidebar, `ui/`, `apiClient`, TanStack Query); auth-rbac (`require_admin`/`AuditEvent`)
- Grounding verified live: `amp_port_hardcoded` 12-port switch + `udp_blocked_port_bitmap` `ARRAY_OF_MAPS[2]`×`ARRAY[1024]×u64` (`word=port>>6`,`bit=1<<(port&63)`), `loader.c::seed_blocked_port_from_env`, `xdpgw-apply` `carry_forward_*` copy the bitmap, `dpstat` `set-bypass`/`set-nexthop` + worker reconcile lanes present; **no CP model/API for blocked ports exists**. New model + migration number pinned live at Execute (head `20260714_0011_alerting`)
- Design `design.md` (**AD-036**) + 2 rendered diagrams (`diagrams/amp-config-architecture.{mmd,svg}` component/data-flow, `diagrams/reconcile-sequence.{mmd,svg}` add→reconcile→enforce). **4 thin tracks** (DP∥CP-API∥CP-worker∥FE) cloning the present-in-code bypass/next-hop pattern 1:1. Key decisions: **T1** propagation = PG desired-state + **worker poll** (~1s, separate processes → no cross-process event; corrects A-AMP-2) · **T2** `dpstat` writes **BOTH** slots' inner maps — carry-forward-safe because `xdpgw-apply` `carry_forward_*` pointer-installs the active inner and **never clears content** (the load-bearing AMP-11 proof) · **T3** model `blocked_udp_port` with `port` natural PK `Integer`+CheckConstraint 0..65535 (SmallInteger too small) · **T4** in-memory `asserted_ports` drift + restart re-assert (clone `asserted_bypass`; loader-reload gap documented, periodic-reassert = Tasks flag F2) · **T5** built-in set = CP constant mirroring DP `amp_port_hardcoded` · **T6** +2 settings (`worker_blocked_port_enabled/_interval`), reuse dpstat binary+timeout · **T8** no Redis JobType / no wire change / no drop-reason change / no new map or pin. New: `BlockedUdpPort` model+migration, `services/ddos_amplification.py`, `/ddos` router+schemas, `worker/blocked_port_reconciler.py`+wiring, `dpstat set-blocked-ports`, SPA `features/config/ddos/*`+hook+nav+route. All 21 reqs mapped to components; 7 flags for Tasks (F1 naming, F2 reassert, F3 P2 read-back, F4 overlap, F5 GET shape, F6 migration#, F7 lane site)
- Tasks `tasks.md` (CT1–CT5 ∥ DT1–DT2 ∥ FT1–FT2 + DOC1 + P2 PT1–PT2; all 21 reqs mapped): **CP** CT1 model+migration · CT2 service+audit+`HARDCODED_AMP_PORTS` · CT3 `/ddos` router+schemas · CT4 reconciler+`DpstatBlockedPortsWriter`+`Fake` · CT5 worker wiring+settings (all integration → serial on `compose.test.yml`) · **DP** DT1 `dpstat set-blocked-ports` (build) · DT2 `smoke_blocked_port.sh` (dp-integration, `make smoke`) · **FE** FT1 types+hook `[P]` · FT2 page+nav+route `[P]` · **DOC1** `[P]` · **P2** PT1 `dpstat snapshot` blocked-ports + PT2 effective-state panel. **All 3 pre-approval checks pass** (granularity — CT4/FT2 flagged cohesive-not-split w/ nexthop/global-blacklist precedent; diagram↔`Depends on`; test co-location — DT1/PT1 `none` = userspace dpstat tooling covered by the privileged DT2/snapshot smoke, DP-established `set-bypass` pattern, not deferral). 3 concurrent tracks (DP∥CP∥FE, disjoint infra); only unit/fe-unit parallel-safe so CP integration = one serial chain. Baselines pinned live at Execute (`B_cp`≥507, `B_dp`=130, `B_fe`≥213). Tools: `coding-guidelines` code, `docs-writer` DOC1, no MCPs
- **Executed (2026-07-22, 16 atomic commits `181223a..80a67ff`):** CT1–CT5 ∥ DT1–DT2 ∥ FT1–FT2 + DOC1 all landed (P2 PT1/PT2 intentionally deferred). Migration pinned live to `20260722_0013_blocked_udp_port` (head at Execute was service-ratelimit's `_0012`, not the drafted `_0011_alerting`). Final gates all green: **CP** `ruff`/`ruff format`/`mypy` clean + feature's 33 integration tests pass (`test_blocked_udp_port_model` 6, `test_ddos_amplification_service` 6, `test_ddos_router` 3, `test_blocked_port_reconciler` 4, `test_worker_runtime` +2 lane, `test_udp_amplification_e2e` 1); **FE** lint/typecheck/**220 tests (48→50 files, B_fe 213→220, +7)**/build all green; **DP** `make bpf skel loader apply dpstat` clean + `make test` **137** + DT1 behavior verified (usage lists `set-blocked-ports`; unloaded→friendly exit 1; port>65535→exit 2) + **DT2 `smoke_blocked_port.sh` privileged veth smoke PASSED** (src-port 9999 drops `udp_amplification_drop`/9998 delivered; **survives `xdpgw-apply` slot-swap = carry-forward-safe AMP-11**; empty set clears). Reconciler is a 1:1 clone of `NodeControlReconciler` — drift-only on in-memory `asserted_ports` (None→re-assert on restart), fail-safe on write failure (never clears asserted → AMP-10). **Caveat (whole-repo `pytest -q` = 610 passed / 6 FAILED):** all 6 failures are **pre-existing and unrelated** — the feature touches zero alert/user code (verified via `git diff --name-only`); 2 are M6-Alerting (`test_alert_models`/`test_alerts_api`, that feature is still IN-PROGRESS) and 4 are user-deletion tests that **pass in isolation** but fail under full-suite test-ordering pollution (`delete_user` coroutine leak). See memory `[[udp-amp-config-verified]]` + `[[cp-suite-preexisting-failures]]`
- **Deferred (P2, optional):** PT1 `dpstat snapshot` blocked-ports read-back (AMP-20) + PT2 DDoS Protection effective-state panel (AMP-21). Minor: CT3 router test has 3 functions vs the drafted ≥5 floor, but all required scenarios (GET 12-hardcoded/201/409/422/204/404/tenant-403) are covered via multi-assertion tests — coverage complete

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
