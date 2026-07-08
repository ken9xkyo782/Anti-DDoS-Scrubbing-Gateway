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

**Drop-reason counters** - PLANNED
- Per-CPU `counter_map`; standardized drop reasons (10.2); rate-limited ringbuf/perf sampling

---

## M3 — Policy enforcement & fairness

**Goal:** Full verdict pipeline — allow-rules, rate-limits, scoped whitelist/VIP, blacklists, amplification/bogon filters, and per-service committed clean-bandwidth reservation.
**Target:** Pipeline of section 8.2 fully enforced; fairness test passes (flooding service A never starves service B's committed bandwidth).

### Features

**Allow-rule matching & rate-limit** - PLANNED
- First-match by ascending `priority`, terminal verdict, early-exit on `rule_count`
- Per-CPU aggregate token buckets (`rate_limit_state`); `rate_limit_drop`, no fall-through

**Whitelist/VIP (scoped) & VIP ceiling** - PLANNED
- Bloom → LPM keyed by `service_id`+source CIDR (no cross-service bypass)
- VIP ceiling aggregate bucket; `vip_ceiling_drop`

**Blacklist (bloom + LPM)** - PLANNED
- Global + service blacklist via bloom → LPM; `blacklist_drop`; `bloom_hit_lpm_miss` counter
- Hardcoded UDP amplification ports, bogon check, dynamic blocked-port bitmap

**Fairness & bandwidth reservation (8.4)** - PLANNED
- 2-tier committed (global + spin_lock) / burst (per-CPU) buckets per service
- Node headroom bucket (`congestion_drop`); ingress-cost cap (`ingress_cap_drop`); `service_ceiling_drop`

---

## M4 — Worker sync & threat feed

**Goal:** Python worker consuming Redis jobs that rebuilds BPF maps and swaps them atomically via double-buffer, plus scheduled threat-feed ingestion.
**Target:** A control-plane change reaches active data-plane ≤ 5 s; failed builds keep the previous active slot; feed sync is resilient per source.

### Features

**Agent worker & job pipeline** - PLANNED
- Jobs: `SERVICE_UPDATE`, `RULE_UPDATE`, `LIST_UPDATE`, `FEED_SYNC`, `MAP_REBUILD`, `ACTIVE_SLOT_SWAP`, `TELEMETRY_AGGREGATE`
- Idempotent by `job_id`/version; no stale-over-new swap; worker restart preserves active state

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
