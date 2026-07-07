# Anti-DDoS Scrubbing Gateway

**Vision:** A single-node L3/L4 DDoS scrubbing gateway that classifies traffic in real time at the XDP/eBPF layer, drops malicious volumetric traffic, and transparently forwards clean traffic from `IN` to `OUT` as an L2 inbound-only bridge.
**For:** Internal tenants (paying via internal chargeback) whose IP/CIDR ranges sit behind the scrubber, plus a system admin who provisions tenants, ranges, and threat feeds.
**Solves:** Volumetric L3/L4 DDoS (UDP/SYN/ICMP flood, port scan, UDP reflection/amplification) hitting protected ranges, while guaranteeing each tenant its committed clean bandwidth even when a neighbor is under attack.

## Goals

- **Throughput:** ≥ 40 Gbps or 20 Mpps per node in native-XDP benchmark.
- **Latency:** added p99 ≤ 1 ms on clean traffic.
- **Config propagation:** service/rule/list/feed change reaches the data-plane ≤ 5 s.
- **Realtime dashboards:** service-level metrics refresh ≤ 2 s.
- **Clean accuracy:** zero known false drop within the v1 test set (IPv6/fragment/malformed explicitly out of the pass commitment).
- **Fairness/SLA:** each service is guaranteed up to `committed_clean_gbps` even when another service on the shared node is flooded.
- **Scale envelope:** ≤ 100 tenants, 1,000 services, 16 rules/service, 1M global blacklist entries.
- **Chargeback:** meter clean throughput by p95 clean Gbps and export `BillingUsage` for internal chargeback.

## Tech Stack

**Core:**

- Data-plane: **C — XDP/eBPF** (native driver mode required), libbpf; `XDP_REDIRECT` via devmap `IN → OUT`.
- Sync worker: **Python** — consumes jobs from Redis, rebuilds/swaps BPF maps.
- Job queue: **Redis**.
- Control-plane API: **Python + FastAPI**.
- Database: **PostgreSQL** (native `inet`/`cidr` types for CIDR allocation & overlap checks).
- Dashboard: **React (SPA)** — tenant + admin, realtime telemetry.

**Key techniques:** bloom filter → LPM trie for allow/deny lookups; per-CPU token buckets & counters; double-buffer `active_slot` for atomic config swap; 2-tier committed/burst + node-headroom buckets for per-service fairness; `argon2`/`bcrypt` password hashing.

## Scope

**v1 (Pilot MVP) includes:**

- Native XDP verdict pipeline on `IN`, redirect clean traffic to `OUT` (L2 transparent bridge, inbound-only, header-preserving).
- Volumetric L3/L4 filtering: UDP/SYN/ICMP flood, port scan, UDP reflection/amplification (rate-limit + blacklist based).
- Service allowlist (IP/CIDR, protocol, port ranges) with first-match-by-priority allow-rules and aggregate PPS/BPS rate-limits.
- Whitelist/VIP (service-scoped bypass, subject to VIP ceiling); tenant/service blacklist; global blacklist + scheduled threat-intel feed.
- Per-service fairness & committed clean-bandwidth reservation (2-tier buckets, node headroom, ingress-cost cap).
- Python worker + Redis job pipeline; double-buffer map rebuild/swap with rollback.
- Tenant + admin dashboards; service-level telemetry; p95 clean-Gbps chargeback (`ServicePlan`/`BillingUsage`).
- Auth/RBAC with strict tenant isolation; audit log; alerting (email/webhook); global bypass + maintenance mode.

**Explicitly out of scope (v1):**

- WAF / L7 / HTTP inspection / reverse proxy.
- Full IPv6 forwarding (IPv6 is hard-dropped in v1); IPv4 fragments dropped.
- HA / failover / clustering (single-node only).
- BGP dynamic routing / Flowspec / route advertisement.
- Dynamic MAC/bridge learning; full packet-level forensics / payload capture.
- Auto-mitigation / auto-rule generation (v1 is manual config only).

## Constraints

- **Availability:** Pilot is single-node, fail-closed inline (SPOF) → Availability is **best-effort, NOT under SLA** at Pilot (maintenance window + bypass in the OLA). HA/failover is a **GA blocker** (CM-01) and the condition for an Availability commitment.
- **Technical:** native XDP driver mode mandatory (generic mode = alert); multi-queue NIC + symmetric RSS; no per-source-IP state on the hot path (anti hash-map thrashing); hot-path billing bytes must be exact per-CPU counters, separate from rate-limited event sampling.
- **Commercial model:** internal, paid via chargeback (Gbps clean, p95). Not sold externally.
- **Open before Pilot (non-blocking, non-engineering):** IPv6 blackhole onboarding warning (CM-02), capacity positioning (CM-06), threat-feed license review (CM-07).
