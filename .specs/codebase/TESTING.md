# Testing

**Stack:** Python 3.12+ · pytest · **async** (asyncpg + SQLAlchemy 2.0 `AsyncSession`, `redis.asyncio`) · httpx `AsyncClient` + `pytest-asyncio` · ruff + mypy.
**Integration infra:** developer/CI starts `compose.test.yml` (Postgres + Redis on fixed local ports); tests connect to them.

> Greenfield conventions established here (2026-07-07) for the control-plane. Data-plane (C/XDP) and worker testing are defined by their own milestones.

## Test Types

| Type | Definition | Needs | Marker |
| --- | --- | --- | --- |
| **unit** | Pure logic, no I/O | nothing | `@pytest.mark.unit` |
| **integration** | Exercises Postgres and/or Redis, or the full HTTP path | `compose.test.yml` up | `@pytest.mark.integration` |

No mocking of the DB/Redis for integration tests — use the real services (PG-specific features `citext`/`JSONB`/`CHECK` make SQLite/fakeredis unfaithful). Each integration test runs in a rolled-back transaction or a truncated schema for isolation.

## Test Coverage Matrix

| Code layer | Location | Required test type |
| --- | --- | --- |
| Config / app factory / lifespan wiring | `app/core/config.py`, `app/main.py` | none (import smoke) |
| DB engine / session / Alembic harness | `app/db/session.py`, `migrations/` | integration |
| Models + constraints | `app/db/models.py` | integration |
| Security primitives (hash/verify, session id) | `app/core/security.py` | unit |
| Redis session store | `app/core/sessions.py` | integration |
| Audit service | `app/services/audit.py` | integration (unit for scrub helper) |
| Auth dependencies / guards | `app/core/deps.py` | integration |
| User service | `app/services/users.py` | integration |
| Auth service | `app/services/auth.py` | integration |
| API routers | `app/api/routers/*.py` | integration (API via AsyncClient) |
| Bootstrap CLI | `app/cli.py` | integration |
| Worker backoff and Redis-pop helper | `app/worker/worker.py` | unit: `tests/unit/test_worker_backoff.py` |
| Worker processor and reconciliation | `app/worker/processor.py` | integration: `tests/integration/test_worker_processor.py` |
| Worker runtime and module entrypoint | `app/worker/worker.py`, `app/worker/__main__.py` | integration: `tests/integration/test_worker_runtime.py` |

**Rule:** a task creating any layer above writes that layer's tests **in the same task** (highest type wins if it spans layers). `Tests: none` only where the matrix says none.

## Gate Check Commands

| Gate | Command | When |
| --- | --- | --- |
| **quick** | `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit` | unit-only tasks; fast inner loop |
| **full** | `ruff check . && ruff format --check . && mypy app/ && pytest -q` (requires `compose.test.yml` up) | any task with integration tests |
| **build** | `python -c "import app.main"` (import smoke) + `alembic upgrade head` on test DB | skeleton / wiring tasks |

Every task's **Done when** cites the expected pass count (e.g. "N tests pass") to prevent silent test deletion.

## Parallelism Assessment

| Test type | Parallel-Safe | Why |
| --- | --- | --- |
| **unit** | **Yes** | No shared state; safe to run in concurrent sub-agents |
| **integration** | **No** | All integration tests share the single `compose.test.yml` Postgres + Redis (fixed ports); concurrent sub-agents running integration suites collide on shared state |

**Consequence for `[P]`:** only tasks whose required test type is **unit** may be marked `[P]`. Any task with integration tests runs **sequentially**, even when its code has no dependencies — the shared test infra is the bottleneck.

## Conventions

- Test files: `tests/unit/test_*.py`, `tests/integration/test_*.py`.
- Async tests: `pytest-asyncio` in `auto` mode; fixtures yield `AsyncSession` / `redis.asyncio` clients bound to `compose.test.yml`.
- Shared fixtures in `tests/conftest.py`: `db_session` (transaction-rolled-back), `redis_client` (flushed per test), `client` (httpx `AsyncClient` over the ASGI app), `admin_principal` / `tenant_principal` factories.
- Worker real-commit tests use `committed_db` in
  `tests/integration/conftest.py`, not the rollback-isolation `db_session`
  fixture. It truncates the affected tables before and after each test so
  `session_scope` commits remain isolated.
- Inject `RecordingApplier`, `FailingApplier`, or `BarrierApplier` into worker
  processor and runtime tests. These appliers make acknowledgments, failures,
  and mid-apply coordination deterministic without touching the data plane.
- Secrets: tests never assert on plaintext passwords; a log-capture fixture asserts **no** credential material is emitted (Success Criteria).

### Redis-down worker verification

The Redis-down check is deliberately isolated and manual.
`WORKER_REDIS_DOWN_TEST` is unset, and no automated gated case verifies this path.
Do not report it as having passed automatically. Run this procedure alone, not
alongside integration tests:

1. Stop test Redis:

   ```sh
   docker compose -f control-plane/compose.test.yml stop redis
   ```

2. Commit a normal service update while Redis is down.

3. Run the worker and verify its DB-ledger reconciliation reaches `active`
   without a failed job.

4. Restart Redis:

   ```sh
   docker compose -f control-plane/compose.test.yml start redis
   ```

5. Verify the `Redis connection resumed` log, then verify a new ordinary enqueue
   reaches `active` via BRPOP within 5 seconds.

---

## Data-plane (C/XDP)

**Stack:** C · XDP/eBPF · libbpf skeletons · `BPF_PROG_TEST_RUN` synthetic packet tests.
**Location:** `data-plane/`

### Data-plane Test Types

| Type | Definition | Needs | Parallel-Safe |
| --- | --- | --- | --- |
| **dp-unit** | Runs the verifier-approved XDP program through `BPF_PROG_TEST_RUN` with synthetic frames and asserts verdicts, drop counters, sampled drop events, sample stats, and test-only `pkt_meta` output | BPF-capable kernel and permission to load BPF programs | **Yes** as infrastructure; individual parse tasks serialize when they edit shared parser/test files |
| **dp-integration** | Runs a privileged two-veth `IN` to `OUT` smoke through the native-XDP loader, seeds one enabled service, sends a crafted IPv4 frame, and asserts real `XDP_REDIRECT` delivery with TTL and IPv4 checksum unchanged | `CAP_NET_ADMIN`/root, veth with XDP redirect support, `clang`, `bpftool`, Python 3 | **No**; shared interfaces/kernel attach state |

### Data-plane Gate Check Commands

Run these from `data-plane/`.

| Gate | Command | When |
| --- | --- | --- |
| **build** | `make bpf skel loader apply dpstat` | Scaffold, loader, apply helper, observability tooling, and wiring tasks. `make apply` also builds `test_snapshot` and runs the snapshot parse self-test against the golden fixture |
| **quick** | `make test` | Parser/verdict tasks with `dp-unit` coverage |
| **full** | `make test && sudo make smoke` | Pre-merge checks on a BPF+veth-capable runner; `make smoke` runs the redirect, fairness, and apply smokes and is privileged and not parallel-safe |
| **scale (blacklist)** | `sudo make blbulk` | Privileged 1M global-blacklist load and footprint check; run only when blacklist map capacity or kernel memory posture changes |
| **scale (apply)** | `sudo make applybulk` | Privileged 1000-service build/verify/flip; asserts a sub-5 s wall time, exactly one `active_config` flip, and feed-owned maps carried forward. Run when the apply path or the slotted config-map set changes |

The native-mode loader is verified by the build gate plus a manual veth/NIC smoke:
`SERVICE_DEST=<ipv4-or-cidr> sudo ./build/xdp_gateway_loader <in-ifname> <out-ifname>` should attach to
IN in native/DRV mode, populate `tx_devmap[0]` with OUT, pin observability maps under
`/sys/fs/bpf/xdp_gateway/`, seed the demo service when provided, or fail clearly with no generic/SKB
fallback. Ctrl-C should detach cleanly and remove the pins.

### Drop-reason ABI

`data-plane/src/drop_reason.h` is authoritative for drop-reason names and indices. Indices `0..15` are
frozen and append-only; future reasons use index `16+` within `DROP_REASON_CAP=32`.

| Index | Name |
| --- | --- |
| 0 | `ipv6_unsupported` |
| 1 | `unsupported_ethertype` |
| 2 | `malformed_ipv4` |
| 3 | `fragment_unsupported` |
| 4 | `bogon_drop` |
| 5 | `service_miss` |
| 6 | `service_disabled` |
| 7 | `udp_amplification_drop` |
| 8 | `blacklist_drop` |
| 9 | `not_allowed` |
| 10 | `rate_limit_drop` |
| 11 | `service_ceiling_drop` |
| 12 | `congestion_drop` |
| 13 | `ingress_cap_drop` |
| 14 | `vip_ceiling_drop` |
| 15 | `map_error` |

### Sampling conventions

Drop sampling uses `drop_ringbuf` with per-CPU token buckets. Tests set `sample_config` to
`rate_per_sec=0, burst=B` to make the budget deterministic: exactly `B` drops can emit events before
later drops increment `SAMPLE_SUPPRESSED`. The `BPF_PROG_TEST_RUN` suite consumes ringbuf events
directly; the de-risk case passes in this environment. Exact `counter_map` totals must remain correct
when samples are emitted, suppressed, or lost.

### Rule-stage conventions

Allow-rule tests seed `rule_block_0/1` with `seed_rule_block()`. Existing enabled-service cases use a
match-all, no-quota rule block so service lookup behavior stays explicit after enabled services become
default-deny. An absent block or a block with `rule_count=0` must drop with `not_allowed`.

Rate-limit tests pin the runner to CPU 0, set `rl_ncpus` rodata before BPF load, and set
`rl_config.test_no_refill=1` when exact quotas are required. In deterministic mode, a rule's `pps` and
`bps` values are the exact per-CPU token budgets, and refill is disabled.

### Whitelist-stage conventions

Whitelist tests seed services first, then call `seed_whitelist()` to populate the slot's bloom,
scoped LPM, VIP config, and `service_val.wl_flags`. Use lower-level helpers only for structural cases
such as a bloom false positive, an inactive `WL_F_ACTIVE` gate, or a missing map inner. Bloom filters
are replace-only, so tests use fresh skeleton instances or distinct keys instead of trying to clear a
bloom map.

The whitelist and allow-rule buckets share `rl_config.test_no_refill`. Set it to `1` when asserting
exact VIP ceiling counts; in deterministic mode, VIP `pps` and `bps` values are exact per-CPU token
budgets, just like rule quotas. VIP overflow is terminal: assert `DR_VIP_CEILING_DROP` at index 14 and
verify overflow packets don't fall through to the rule stage.

### Fairness-stage conventions

`seed_service()` supplies a generous `fair_config` row for normal enabled-service cases. Fairness
tests that need constrained budgets overwrite that row with `seed_fair_config()` and seed the
active slot's `fair_node_config` with `seed_fair_node_config()`. Use the lower-level service-only
setup only to prove the required missing-fairness-row `map_error` failure case.

Set `rl_config.test_no_refill=1` for every exact fairness quota. Committed-bucket assertions do
not depend on CPU pinning because `svc_committed_state` is global and spin-locked. Burst, node
headroom, and ingress-cap assertions use the CPU-pinned runner and the same no-refill setting,
because those buckets are per-CPU. When testing a burst miss at the node, assert the
service-then-node order: the service burst token is consumed first and is not refunded when the
node has no headroom.

The deterministic fairness milestone uses two enabled destination services. Interleave service A's
flood with service B's committed traffic, then repeat B's traffic in a fresh no-A-flood control
setup and assert equal B admission counts. Cover A's three terminal conditions independently:
ingress-cap exhaustion (`ingress_cap_drop`), service-ceiling exhaustion
(`service_ceiling_drop`), and node-headroom exhaustion (`congestion_drop`). This is a dp-unit
scenario; the live smoke remains a constrained single-service transition check.

### Deny-stage conventions

Deny-stage tests seed services first, then use the blacklist helpers to populate slotted global
blacklist bloom/LPM maps, service-scoped bloom/LPM maps, `gbl_meta`, and the UDP blocked-port
bitmap. Use lower-level bloom-only helpers for deterministic false-positive induction. A bloom hit
with an LPM miss must increment exactly one `bloom_stats` stage counter and must not change
drop-reason counters.

Packet sources in new non-bogon tests must come from the named public pools in
`data-plane/tests/pkt_build.h`, such as `TEST_SRC_PUB_A`, `TEST_SRC_PUB_B`, and
`TEST_SRC_PUB_C`. Keep RFC 1918, TEST-NET, loopback, multicast, and reserved ranges only for tests
that deliberately assert `bogon_drop`.

Bloom filters are replace-only. Tests use fresh skeleton instances or distinct keys instead of
trying to clear bloom values. Use missing map inners for fail-closed coverage, because valid
`ARRAY` slots such as `gbl_meta[0]` and `gbl_meta[1]` are not deletable.

Run `sudo make blbulk` when you change blacklist map definitions, bloom/LPM key formats, or kernel
capacity assumptions. The target is not part of `make test`; it loads the normal skeleton, inserts
1,048,576 mixed `/24` and `/32` global blacklist entries, checks sampled bloom/LPM membership, and
runs one `BPF_PROG_TEST_RUN` packet that must return `XDP_DROP`.

### Apply-helper (`xdpgw-apply`) conventions

The `xdpgw-apply` build core is factored into fd-taking functions —
`build_inactive_slot`, `carry_forward_feed`, `verify_slot`, `commit` — so the whole
build → verify → flip → verdict path runs in-harness under `make test`. `tests/test_parse.c`
`#include`s the helper (its `main()` is elided) and drives the core with skeleton fds, then replays a
`BPF_PROG_TEST_RUN` packet to confirm the flip changed enforcement. Only the `main()` pin-open plus the
subprocess CLI need the privileged smoke.

Apply dp-unit cases seed the live slot, run the core against a snapshot, and assert the flip. Cover the
build-then-flip verdict change (a newly allowed flow admits, a removed service misses) alongside the
carry-forward invariant (feed-owned global-deny maps and non-triggering services are unchanged after the
flip) and the fail-closed paths (a forced build-install or `verify_slot` mismatch leaves `active_config`
untouched; two applies of one snapshot bump `version` V→V+1→V+2 with identical verdicts). Faults are
injected through `apply_test_set_fault()`, compiled only under `-DXDPGW_APPLY_TEST`; the helper binary
has no runtime switch that can force a production apply to fail.

The wire format is the `apply_snapshot.h` v1 contract (magic `XDPGWAP1`, `schema_version`, per-service
record with service-level VIP and the `dp_id` surrogate). `make apply` runs `build/test_snapshot`
against the committed `tests/fixtures/apply_snapshot_golden.bin`; the control-plane
`serialize_node_snapshot` must emit byte-identical output for the matching node, so the fixture binds the
C parser and the Python serializer. Bump `schema_version` in the header and regenerate the fixture
(`tests/fixtures/gen_apply_snapshot_golden.py`) together on any layout change — readers reject an unknown
version before touching a map.

`sudo make applybulk` is the scale gate: it loads the skeleton, generates a 1000-service snapshot, runs
one apply, and asserts the build/verify/flip completes in under 5 s, that `active_config` flips exactly
once (slot 0→1, version 1→2), and that the feed-owned `global_blacklist_bloom`/`_lpm` and
`udp_blocked_port_bitmap` inner ids are carried forward rather than rebuilt. It is not part of
`make test`; run it when the apply path or the slotted config-map set changes.

### Data-plane Corpus

`dp-unit` tests use adversarial synthetic frames, including IPv6, unsupported EtherTypes, runt Ethernet,
malformed IPv4, first and later IPv4 fragments, truncated L4 headers, ARP, single VLAN, QinQ, too-deep
VLAN stacks, service lookup verdicts, allow-rule matching, deterministic per-rule rate limits,
whitelist scoped-match and VIP ceiling cases, blacklist amp-port, bogon, bitmap, global/service
bloom-to-LPM, bloom false-positive, sampling budget, and `xdpgw-apply` build/verify/flip, fresh-inner,
and fail-closed rollback cases. The current quick suite has **122**
tests. Each verdict task states the expected passing test count to prevent silent deletions or skipped
coverage.
