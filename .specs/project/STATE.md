# State

**Last Updated:** 2026-07-09
**Current Work (planning track):** M3 → **Whitelist/VIP (scoped) & VIP ceiling** — **spec APPROVED + context + design drafted** (2026-07-09): `.specs/features/whitelist-vip/` spec.md (WLV-01..25) + context.md (**D-WLV-1**: NULL `vip_pps`+`vip_bps` = whitelist **inactive** — AD-020; A-WLV-1..8) + design.md (**AD-021**: composite scoped LPM key prefixlen≥32, /24 bloom buckets + `WL_F_HAS_BROAD` escape, `service_val.wl_flags` zero-cost D-WLV-1 gate, slotted `vip_config_map` + `rl_bucket` reuse with `rules.h` untouched, VIP admit → `redirect_out()` not `admit_clean()`, bloom replace-only M4 contract, de-risk ladder BTF-static→loader-created→LPM-only) + 2 rendered diagrams (`diagrams/whitelist-{stage-flow,map-layout}.{mmd,svg}`). Scoped bloom→LPM bypass (key incl. `service_id`, BL-01/02) inserted between service-enabled hit and ARL's rule stage; hit → VIP ceiling (aggregate per-service, per-CPU, unslotted `vip_ceiling_state`, D-ARL-2 lazy reset + AD-019 rate÷nCPU precedent) → redirect (skips 8.4 admit ladder); over-ceiling = terminal `vip_ceiling_drop` (frozen ABI **index 14**, dpstat zero-change); miss → rule stage unchanged (post-ARL baseline passes as-is). Slotted whitelist maps = M4 contract; seed-helper interim writer; marked seams for M3#3 (miss path) & M3#4 (ingress cap). Design **APPROVED** (2026-07-09) + **tasks.md drafted** (T1–T5, all 25 reqs mapped; baseline **B=50** — ARL executed, A-WLV-8 gate satisfied): T1 contracts+maps+bloom de-risk ladder (build+quick, 51) · T2 scoped match stage+wire+seams (dp-unit, ≥60) · T3 VIP ceiling bucket idx 14 (dp-unit, ≥66) · T4 loader env seed+smoke (full gate) · T5 docs `[P]`. Only T5 parallel. Tasks **APPROVED** (2026-07-09) → next session: **Execute** (T1 first). Requires ARL executed first (A-WLV-8; shared hot-path files).
**Whitelist/VIP Execute Progress:** T1 complete/verified (2026-07-09): primary static `BLOOM_FILTER` inner-in-`ARRAY_OF_MAPS` rung loaded successfully; de-risk dp-unit push/lookup/peek case passed; gate `cd data-plane && make bpf skel loader dpstat && make test` → **51 passed**.
**Execute track:** M3 → **Allow-rule matching & rate-limit** — spec approved + context + **design drafted** (2026-07-09): `.specs/features/allow-rule-ratelimit/` spec.md (ARL-01..25) + context.md (D-ARL-1 strict `any`; D-ARL-2 reset-on-swap — AD-018) + design.md (AD-019: pre-sorted `rule_block_map` ARRAY_OF_MAPS[2]/HASH, lazy version-reset `PERCPU_HASH` buckets, rate÷nCPU split, `rules.h` = M4 contract) + 2 rendered diagrams (design APPROVED) + **tasks.md drafted** (T1–T5, all 25 reqs mapped): T1 contracts+maps+verifier de-risk (build+quick, 34 unchanged) · T2 match engine+wire+suite migration (dp-unit, ≥42) · T3 buckets+deterministic mode (dp-unit, ≥49) · T4 loader seed+`rl_ncpus`+live smoke (full gate) · T5 docs `[P]`. Only T5 parallel (T1–T3 share files; T4 smoke not parallel-safe). Tasks APPROVED → **Executed / VERIFIED** (2026-07-09): T1–T5 committed (`d220628`..`4d87f4b`); final gates passed — `make test` → **50 passed** (re-verified during whitelist-vip Tasks session), rule-stage conventions + tunnel-traffic note documented in TESTING.md/README. A-WLV-8's execute gate is satisfied.
**Packet Parse Execute Status:** Complete and verified (2026-07-08). T1-T8 are committed; final gates passed: `cd data-plane && make bpf skel loader` and `cd data-plane && make test` -> 21 passed. B-002 remains resolved in this environment.
**Prior M2 work (drop-reason counters):** **VERIFIED / executed** (2026-07-08). T1-T6 are committed; final gates passed: `cd data-plane && make test` -> 34 passed and `cd data-plane && make bpf skel loader dpstat` -> passed. `./build/dpstat counters` without pinned maps returns the expected gateway-not-loaded error. `BPF_PROG_TEST_RUN` -> ringbuf delivery succeeded in the de-risk case, so no stats-only fallback was needed.
**M2 Execute Status:** Packet parse, service lookup & transparent redirect, and drop-reason counters are verified. Resume = M3 policy enforcement feature planning/execution.
**Prior M2 work (service lookup):** **Service lookup & transparent redirect** — **VERIFIED / executed** (`.specs/features/service-lookup-redirect/`, SLRD-01..26 -> T1-T7). Final gates: `make test && sudo make smoke` -> 29 dp-unit tests + live-veth TTL/checksum unchanged.
**Prior M2 work (packet parse):** **Packet parse & fail-fast** — **VERIFIED / executed** (`.specs/features/packet-parse/`, PKT-01..24 -> T1-T8). Final gates: build=`make bpf skel loader`; quick=`make test` -> 21 passed.
**Prior M1 work (all awaiting approval → next phase):** **Apply-status state machine** — spec + context + design + **tasks** complete (`.specs/features/apply-status/`, APLY-01..40 → T1–T7; D-APLY-1..3; A-APLY-1..6): `pending→queued→applying→active|failed` behind one guard, API auto-enqueue (real Redis + version-idempotent `AgentJob` ledger via transactional outbox), version-guarded worker-facing `mark_*`, per-service apply-status read API + admin job-list; `agent_job` table + `core/applystate.py` guard + `services/apply.py`; modifies service-rule-list services + `bump_version` (worker loop = M4). **Service, rule & list management (API)** — spec + context + design complete (`.specs/features/service-rule-list/`, SRL-01..44; D-SRL-1..4) → Tasks. **Tenant & CIDR allocation** — spec + design + tasks complete (`.specs/features/tenant-cidr/`, TCA-01..32 → T1–T7) → Execute. **Auth & RBAC** complete (T1–T12, AUTH-01..39) → Execute.

---

## Recent Decisions (Last 60 days)

### AD-021: Whitelist/VIP — data-plane design (composite scoped LPM key, /24 bloom buckets, zero-cost D-WLV-1 gate, VIP bucket reuse) (2026-07-09)

**Decision (design.md):** (a) **Scoping by key construction:** `whitelist_lpm` = `ARRAY_OF_MAPS`[2] of `LPM_TRIE` inners with composite key `{prefixlen, service_id(be32), src(be32)}` (max_prefixlen 64); every entry carries `prefixlen = 32 + cidr_len ≥ 32` so all `service_id` bits are always fully matched — BL-02 needs no runtime check. (b) **Bloom = /24 buckets:** `whitelist_bloom` = `ARRAY_OF_MAPS`[2] of `BLOOM_FILTER` inners (value = 8-byte `{svc_be, src&/24}`, 65536 entries, 5 hashes); entries with cidr ≥ /24 contribute exactly one key; broader entries set per-service `WL_F_HAS_BROAD` → skip bloom, always LPM-confirm (no 2^k key blow-up; degradation scoped to the owning service). Blooms are **replace-only** (no kernel clear/delete) → M4 builds a fresh inner per swap; meta-equal params (value_size/max_entries/map_extra) pinned in the contract. (c) **Zero-cost D-WLV-1 gate:** `service_val` gains `wl_flags` in a pad byte (`WL_F_ACTIVE`/`WL_F_HAS_BROAD`, value stays 8 B) — the already-fetched service lookup answers "whitelist active?" with no extra map access; builder sets ACTIVE only when entries exist AND ≥1 ceiling dim set. (d) **VIP ceiling:** slotted `vip_config_map` (HASH svc → {version, flags, pps, bps(bytes/s)}) consulted only on LPM hit; `vip_ceiling_state` = unslotted `PERCPU_HASH` svc → `struct rl_bucket` **reused verbatim** from `rules.h` along with the rate-parametric helpers and the shared `test_no_refill` knob — `rules.h` itself untouched (ARL mid-execution). VIP admit calls `redirect_out()` **directly, not `admit_clean()`** — §8.4.6's "VIP never enters the fairness ladder" becomes structural. `pkt_meta` gains `wl_state` (pad byte; none/miss/hit-admit/hit-drop).
**Reason (verified 2026-07-09, kernel docs + eBPF docs, Context7 unavailable):** bloom map (≥5.16) push/peek from XDP, false negatives impossible, "may be used as an inner map" (`.map_meta_equal` registered); LPM data matched MSB-first big-endian with prefix 8..2048 bits → 64-bit composite scoped key is in-spec; per-CPU-hash/lazy-reset semantics carry over from AD-019 unchanged. The one undocumented *composition* (static bloom inners in BTF `__array(values,…)`) gets a fail-fast de-risk ladder: BTF static → loader-created inners (the M4 flow anyway) → LPM-only fallback (verdict-identical; bloom is cost-only by WLV-04).
**Trade-off:** broad (</24) entries force LPM cost on their whole service; a second LPM walk (8-byte key) on the whitelist path for services with active whitelists; `vip_config` as a separate map costs one hash lookup on the rare hit path (keeps SLRD's 8-byte `service_val` ABI frozen).
**Impact:** M3 Whitelist/VIP (WLV-01..25) — new `src/whitelist.h` (3 slotted maps + 1 runtime map + stage); one-line rewire of `service_lookup_redirect`'s enabled branch (seam A comment for M3#4); seam B (miss path) hosts M3#3; `service.h`/`pkt_meta.h` pad-byte extensions (sizes unchanged, zero test churn); loader default seed whitelist-free (baseline + smoke untouched, WLV-22), env-driven VIP seed for demos. 2 rendered diagrams. Next: **Tasks**.

### AD-020: Whitelist/VIP — NULL VIP ceiling = whitelist inactive (fail-safe) (2026-07-09)

**Decision (D-WLV-1, context.md):** a `ProtectedService` whose `vip_pps` AND `vip_bps` are **both NULL** grants **no whitelist bypass** — the data-plane treats its whitelist as empty (behaves exactly as a clean miss, WLV-06) until at least one ceiling dimension is set. One set dimension governs alone (the NULL dimension is unlimited); **`0` = explicit block** per dimension (whitelisted traffic always drops `vip_ceiling_drop` on it — A-ARL-3 consistent). Spec approved as drafted (WLV-01..25).
**Reason:** PRD §6.5 + risk register call the aggregate VIP ceiling **mandatory** (BL-08 whitelist-spoofing mitigation); NULL=unlimited would make that mitigation opt-in — an uncapped bypass of every defence. Fail-safe option (b) makes an uncapped bypass impossible **by construction**, with no control-plane schema change (fields stay nullable).
**Trade-off:** a whitelist "silently does nothing" until a ceiling is set — UX mitigation (SRL warning when entries exist on a NULL/NULL service + UI banner) captured as a deferred idea, out of this feature's scope. How "inactive" is enforced (builder emits nothing vs kernel-side check) = Design call, verdict identical either way.
**Impact:** M3 Whitelist/VIP feature (WLV-01..25, realizes WLV-13) — second M3 feature; wires frozen ABI index 14 (`vip_ceiling_drop`). Stage inserts between service-enabled hit and ARL's rule stage (hit bypasses rules + future M3#3 filters, subject to VIP ceiling; never the 8.4 admit ladder). Requires ARL executed first (shared hot-path files, A-WLV-8). Agent discretion at Design: bloom granularity over CIDRs (no false negatives), map layout (= M4 contract), `BPF_MAP_TYPE_BLOOM_FILTER` de-risk with LPM-only fallback, VIP bucket key shape, `pkt_meta` outcome field. Onboarding language: "whitelist requires a VIP ceiling to take effect". Next: **Design**.

### AD-019: Allow-rule matching & rate-limit — data-plane design (pre-sorted blocks, lazy version-reset buckets, rate÷nCPU split) (2026-07-09)

**Decision (design.md):** (a) **`rule_block_map` = `ARRAY_OF_MAPS`[2] of `HASH` inners** (`rule_block_0/1`, 1024 entries, `service_id → struct rule_block{version, rule_count, rule_entry[16]}`, 520 B) — same double-buffer shape as AD-015's `service_map`; **rules pre-sorted by ascending `priority` at build time**, so position = match order and `priority` never ships to the kernel (the DB's `UNIQUE(service_id,priority)` guarantees the total order); disabled rows included with `RULE_F_ENABLED` unset (M4 builder stays a dumb row-copier, ARL-05 stays kernel behavior). (b) **D-ARL-2 reset realized lazily**: `rate_limit_state` = preallocated `PERCPU_HASH` `(service_id, rule_idx) → {cfg_version, last_ns, pps_tokens, bps_tokens}`; every touch compares `cfg_version` vs `block.version`, mismatch → reinit to full burst — a slot flip self-resets buckets with **zero worker plumbing**, and the kernel's zero-fill of other CPUs' values on BPF-side element creation funnels first-touch through the same path. (c) **A-ARL-5 resolved: per-CPU rate = configured rate ÷ nCPU** (via `const volatile` rodata `rl_ncpus` set pre-load) with remainder-preserving ns-granular refill (`last_ns` advances by granted time, so quotas < nCPU still admit at the correct long-run rate); documented ARL-14 bound = node admit ∈ [rate/nCPU, rate], **never above configured** (enforcement inverts AD-017's full-budget-per-CPU sampling choice — fail-closed vs don't-starve-observability). (d) map `bps` field = **bytes/sec** (builder converts); `rl_config.test_no_refill` knob + runner CPU-pinning = deterministic dp-unit buckets (quota value = exact admit count).
**Reason (verified 2026-07-09, web + kernel docs, Context7 unavailable):** BPF-side `bpf_map_update_elem`/`lookup` on `PERCPU_HASH` access only the current CPU's slot (race-free under XDP, no atomics); `pcpu_init_value` zero-fills other CPUs' values on creation without `onallcpus` — the two facts that make the lazy-reset scheme sound. Bounded loops (≥5.3) + hash-inner map-in-map confirmed at the first build/load gate (fail-fast, not assumed; SLRD proved the harder LPM-inner case).
**Trade-off:** prealloc `rate_limit_state` costs ~32 MiB at 64 CPUs upfront (accepted: no allocator pressure under flood); rate÷nCPU undershoots when matched traffic concentrates on few RSS queues (documented; aggregate quotas under distributed flood spread well); 2 u64 divisions on quota'd-rule packets only.
**Impact:** M3 Allow-rule feature (ARL-01..25) — new `src/rules.h` (stage + 4 maps) + one-line insert at `service_lookup_redirect`'s enabled hit; `pkt_meta` gains `rule_idx` (pad byte, size unchanged); `admit_clean()` = the marked ARL-24 seam M3#4 replaces; loader seeds a match-all block per service (smoke unchanged); `rules.h` block layout **is** the M4 build contract. 2 rendered diagrams. Next: **Tasks**.

### AD-018: Allow-rule matching & rate-limit — 2 gray areas (strict `any`, buckets reset on swap) (2026-07-09)

**Decision:** (a, **D-ARL-1**) **`protocol = any` matches exactly {tcp, udp, icmp}** (strict PRD §6.4 reading); non-TCP/UDP/ICMP IPv4 protocols (GRE, ESP, …) are unmatchable by any rule → always `not_allowed` — v1 does not carry tunnel/IPsec traffic (documented product statement; explicit `gre`/`esp` protocol values captured as a deferred idea). (b, **D-ARL-2**) **rate-limit buckets reset on config swap** — keyed positionally/per-version, not by stable rule id; every apply of a service's rule/list edits re-grants full burst to that service's rules (bounded, brief). Spec approved as drafted (ARL-01..25). Full context in `.specs/features/allow-rule-ratelimit/context.md` (D-ARL-1..2, A-ARL-1..8).
**Reason:** Strict `any` is the strongest default-deny and matches "ANY trong phạm vi được hỗ trợ"; sustained `not_allowed` from tunnel traffic becomes *expected*, documented behavior. Reset-on-swap needs no rule-identity plumbing through the M4 build contract and keeps the bucket keyspace minimal; the one-extra-burst-per-apply cost is bounded by burst size.
**Trade-off:** Tunnel/IPsec tenants unsupported in v1 (product statement, not a bug); a flood matching a quota'd rule gets one extra burst per config apply during churn.
**Impact:** M3 Allow-rule matching & rate-limit (ARL-01..25) — first M3 feature; turns enabled services **default-deny** (zero rules = drop all, `not_allowed`). Wires frozen ABI indices 9/10 (AD-016/17); rule stage reads a new slotted `rule_block_map` via the SLRD pinned slot (AD-005); `rate_limit_state` unslotted runtime. Agent discretion at Design: per-CPU budget split + deviation bound (A-ARL-5), block map layout, deterministic-bucket test mode, `pkt_meta` matched-rule field. Migrates the 34-case dp-unit suite (enabled-service cases now need a seeded rule). Next: **Design**.

### AD-017: Drop-reason counters — data-plane design (ringbuf sampling, per-CPU token bucket, pinned maps, dpstat) (2026-07-08)

**Decision (design.md):** (a) **sampling channel = `BPF_MAP_TYPE_RINGBUF`** (256 KiB, power-of-2/page-aligned ≈ 8K events of a fixed 32-byte `drop_event`), not perf event array — single shared MPSC buffer, ordering, non-blocking `bpf_ringbuf_reserve` (NULL on full → `SAMPLE_LOST++`, never stalls); (b) **rate limit = per-CPU token bucket** (`sample_bucket` PERCPU_ARRAY) with knobs in a 1-entry `sample_config` ARRAY map (runtime-tunable, no reload; defaults 256 events/s + burst 64 **per CPU**, node bound = rate × CPUs); dp-unit determinism via `rate=0, burst=B`; (c) **`record_drop(meta, reason)` fuses exact counting + best-effort sampling** in one helper (DRC-15) and clamps out-of-range reasons to `DR_MAP_ERROR` (fail-closed); suppressed/lost accounting lives in a separate `sample_stats` map so `counter_map` stays a pure drop-reason ABI; (d) **loader pins** counter/ringbuf/config/stats maps under `/sys/fs/bpf/xdp_gateway/` (fail-loud on existing pins) and a new **`tools/dpstat`** CLI (counters [-w] / tail / rate) consumes pinned paths, decoupled from the loader process.
**Reason (kernel semantics web-verified 2026-07-08, Context7 unavailable):** ringbuf is XDP-compatible (≥5.8), reserve is NULL-on-full by construction, `bpf_ktime_get_ns` available in XDP; §11.1 mandates sampled+rate-limited event emission; per-CPU bucket keeps the drop path free of shared-cacheline atomics (same posture as `counter_map`).
**Trade-off:** budget is per-CPU (node total varies with CPU count — documented); **`BPF_PROG_TEST_RUN`→ringbuf consumability is high-confidence but not explicitly documented** → proven by a fail-fast de-risk case first in the test task, with a documented fallback (dp-unit asserts `sample_stats` accounting only; event content asserted in the gated `make smoke`); pin-path lifecycle assumes one gateway per node.
**Impact:** M2 Drop-reason counters executed T1–T6 (all 17 DRC reqs verified) with 2 rendered diagrams. New `src/drop_event.h` + `src/sample.h` + `tools/dpstat.c`; rewrites `drop_reason.h` (frozen 0..15 ABI per AD-016 + `drop_reason_name[]` + static-assert headroom); extends `loader.c` (pin+seed), `test_parse.c` (symbol-based migration + sampling cases), Makefile. Establishes the bpffs-pinning pattern the M4 worker will reuse for map access.

### AD-016: Drop-reason index ABI — §9.2 doc order, frozen, header name-table as source of truth (2026-07-08)

**Decision (D-DRC-1, context.md):** (a) canonical `enum drop_reason` numbering = **TDD §9.2 document order, indices 0..15** — a one-move migration (`map_error` 4→15, `bogon_drop` takes 4; fail-fast 0–3 and SLRD's `service_miss`/`service_disabled` 5/6 already coincide); (b) **`drop_reason_name[]` string table in `drop_reason.h` is the single source of truth** — tests, the P3 CLI, and the M4 worker decode from it; doc tables reference the header; (c) **append-only growth after 15** within `DROP_REASON_CAP=32`, indices frozen forever after this feature (renumber legal only inside it, existing suite migrated in-place); (d) **SLRD executes first** (ROADMAP order) — no edits to its approved tasks.
**Reason:** M3 adds nine drop paths and M4/M5 consume indices as ABI — last cheap moment to make code and §9.2 agree permanently; doc-order costs only one moved index; a code-adjacent name table can't drift from the enum.
**Trade-off:** `map_error`'s index changes once (its tests + SLRD's `DR_MAP_ERROR` expectations migrate in this feature); adding a GA reason means the doc listing order and index order diverge for reasons >15 (append order wins there).
**Impact:** M2 Drop-reason counters (DRC-01..17): spec + context complete. Closes AD-015's deferred "final §10.2 numbering". Sampling mechanism (ringbuf vs perf), rate-limit policy, and CLI shape = agent discretion at Design. Next: **Design**.

### AD-015: Service lookup & redirect — data-plane design (map-in-map slot, fail-closed redirect, decision-via-test-hook) (2026-07-08)

**Decision (design.md):** (a) **`service_map` = `BPF_MAP_TYPE_ARRAY_OF_MAPS`[2] of `LPM_TRIE` inners** (`service_inner_0/1`, `BPF_F_NO_PREALLOC`), the inner selected by `active_config.active_slot` — the concrete double-buffer for A-SLRD-4; the M4 worker flips one field (`active_slot`) to swap all config atomically. (b) **Redirect is fail-closed by construction:** `bpf_redirect_map(&tx_devmap, 0, XDP_DROP)` returns `XDP_REDIRECT` on a populated `OUT` else `XDP_DROP` (low 2 bits of `flags` = miss action) — no extra hot-path guard. (c) **The redirect decision is observed device-free via `pkt_meta.verdict` in `test_meta_map`** (under `-DPKT_TEST_HOOKS`); real `XDP_REDIRECT` + TTL/checksum only in the gated `make smoke` two-veth path. (d) hot path replaces both packet-parse seams: slot-pin → inner-LPM lookup on `dst_ip` (prefixlen=32, network-order) → `DR_SERVICE_MISS`/`DR_SERVICE_DISABLED`/`DR_MAP_ERROR` or redirect; ARP → same redirect. New `src/service.h`; `drop_reason.h` **appends** 2 reasons (indices stable); `pkt_meta.h` adds `service_id`/`active_slot`/`verdict`; loader gains `OUT`+seed. Full detail in `.specs/features/service-lookup-redirect/design.md`.
**Reason (3 kernel semantics web-verified 2026-07-08, Context7 unavailable):** map-in-map accepts any inner except `PROG_ARRAY` (LPM inner valid); `bpf_redirect_map` uses `flags` low-2-bits as the miss action; **`BPF_PROG_TEST_RUN` does not process `XDP_REDIRECT`** — so redirect forwarding is not unit-testable and the decision must be observable without a device. These are reusable patterns for every M3 config map + redirect path and M4's swap.
**Trade-off:** map-in-map+LPM-inner has no explicit doc example → **fallback** documented (two named top-level LPM maps + slot branch, same external contract) and confirmed at build/load (fail-fast, not assumed); the decision-via-hook means unit tests never assert `retval==XDP_REDIRECT` (only the gated smoke does); appending drop reasons keeps counter indices stable but leaves final §10.2 numbering to *Drop-reason counters* (M2#3).
**Impact:** M2 Service lookup & redirect executed T1–T7 (all 26 SLRD reqs verified). Modifies the executed `data-plane/` (`xdp_gateway.bpf.c`, `pkt_meta.h`, `drop_reason.h`, `loader.c`, `Makefile`, `test_parse.c`) and **migrates the 21 packet-parse tests** whose `XDP_PASS` terminal changed. Establishes the config-map/slot-pin/`tx_devmap` patterns + the first `dp-integration` (`make smoke`) convention (extends `TESTING.md`, A-SLRD-8).

### AD-014: Service lookup & transparent redirect — 3 gray areas (first config maps + slot pin) (2026-07-08)

**Decision:** (a, D-SLRD-1) **this feature owns the config-map read/pin side + a userspace seed helper** — a slot-aware `service_map` (LPM by dst IPv4), an `active_config` map (`active_slot`+`version`), the ingress `active_slot` snapshot/pin into `pkt_meta`, and `tx_devmap`; a loader/test seed helper fills a slot so it's independently loadable/testable. The **authoritative Postgres→map build, verify, and atomic `active_slot` swap + rollback stay M4** (no worker/Redis/DB here). (b, D-SLRD-2) **redirect verified by unit decision + a gated live smoke** — `BPF_PROG_TEST_RUN` asserts the verdict (`XDP_REDIRECT`/`service_miss`/`service_disabled`/`map_error`) in the parallel-safe suite; a separately-gated live two-veth (`IN↔OUT`) smoke asserts real forwarding + **TTL/checksum unchanged** (first dp-integration test). (c, D-SLRD-3) **ARP switches to `XDP_REDIRECT IN→OUT`** via the same `tx_devmap` (true transparent bridge; closes packet-parse's ARP seam D-PKT-2), never mis-counted/dropped; replies return via the asymmetric/DSR path (CM-09). Full context in `.specs/features/service-lookup-redirect/context.md` (D-SLRD-1..3).
**Reason:** The ROADMAP assigns the `active_slot` pin here and it's the invariant every M3 config lookup depends on, so it must be established now with a stable contract M4 can fill without touching the hot path; `BPF_PROG_TEST_RUN` proves the decision but only a live path proves xmit + header preservation, and splitting keeps the everyday suite NIC-free/parallel-safe; a transparent inbound bridge with no useful IP on `IN` should redirect ARP `IN→OUT`, not pass it to a host stack (PRD §8.2 explicitly left ARP "pass/redirect" open).
**Trade-off:** slot machinery whose only writer until M4 is a throwaway seed helper (double-buffer's atomic swap unexercised until M4); header preservation proven in the gated/privileged path, not the quick suite; ARP now depends on the `OUT` driver's `ndo_xdp_xmit` + the asymmetric return path (CM-09).
**Impact:** M2 Service lookup & redirect feature (SLRD-01..26). Introduces the first **config maps** (`service_map`, `active_config`) + `tx_devmap` and the per-packet slot-pin pattern reused by all of M3; replaces packet-parse's service-lookup + ARP seams (PKT-15, PKT-23/24). Adds `DR_SERVICE_MISS`/`DR_SERVICE_DISABLED` to `enum drop_reason` within `DROP_REASON_CAP=32` headroom (A-SLRD-2); full §10.2 set + sampling still *Drop-reason counters* (M2#3). Extends `.specs/codebase/TESTING.md` data-plane section with the redirect/dp-integration convention (A-SLRD-8). No control-plane change. Flagged: A-SLRD-1 `service_map` value = `service_id`+`enabled` (disabled services present in map), A-SLRD-3 loader gains `OUT` arg, A-SLRD-4 slotting mechanism (map-in-map vs slot-in-key) = Design call, A-SLRD-7 map/devmap errors fail closed (`map_error`).

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

## Resolved Blockers

### B-002: Local user could not load BPF programs for `BPF_PROG_TEST_RUN` — Resolved

**Discovered:** 2026-07-08 (Packet parse T3 execution)
**Resolved:** 2026-07-08 — `cd data-plane && make test` now loads the test XDP program and prints `1 passed`.
**Impact:** Previously blocked T3+ verification because `make test` needs to load the XDP program for `BPF_PROG_TEST_RUN`; per plan, unverified task work was not committed or marked complete.
**Evidence:** `make test` compiles the test runner, then libbpf fails to load `xdp_gateway_test_bpf` with `Operation not permitted`; `kernel.unprivileged_bpf_disabled=2`; current shell has no effective capabilities; `sudo -n make test` fails with `sudo: a password is required`.
**Workaround:** Run the gate in a privileged shell (`sudo make test`) or grant an appropriate development environment with BPF load permissions.
**Resolution:** Verified in the current shell with `cd data-plane && make test` → `1 passed`; T3 marked verified and execution continues.

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
- [ ] Explicit tunnel protocol values (`gre`, `esp`, …) in `AllowRule.protocol` so tunnels become allowlistable without widening `any` (D-ARL-1 follow-on, GA) — Captured during: allow-rule-ratelimit discuss (2026-07-09)
- [ ] Control-plane warning/validation + UI banner when whitelist entries exist on a service with both `vip_pps`/`vip_bps` NULL (whitelist inert per D-WLV-1/AD-020; SRL UX follow-up, pairs with M6 VIP-ceiling-hit alert) — Captured during: whitelist-vip discuss (2026-07-09)

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
