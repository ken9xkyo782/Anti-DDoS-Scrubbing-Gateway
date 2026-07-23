# Testing

**Stack:** Python 3.12+ · pytest · **async** (asyncpg + SQLAlchemy 2.0 `AsyncSession`, `redis.asyncio`) · httpx `AsyncClient` + `pytest-asyncio` · ruff + mypy.
**Integration infra:** developer/CI starts `compose.test.yml` (Postgres + Redis on fixed local ports); tests connect to them.

> Greenfield conventions established here (2026-07-07) for the control-plane. Data-plane (C/XDP) and worker testing are defined by their own milestones.

## Test Types

| Type | Definition | Needs | Marker |
| --- | --- | --- | --- |
| **unit** | Pure logic, no I/O | nothing | `@pytest.mark.unit` |
| **integration** | Exercises Postgres and/or Redis, or the full HTTP path | `compose.test.yml` up | `@pytest.mark.integration` |
| **fe-unit** | React component, hook, route, or API-client behavior in Vitest/jsdom | `control-plane/frontend/node_modules` | Vitest `*.test.ts(x)` |

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
| Billing period and metrics helpers | `app/services/billing_period.py`, `app/services/billing_metrics.py` | unit: `tests/unit/test_billing_period.py`, `tests/unit/test_billing_metrics.py` |
| Billing meter | `app/worker/billing.py` | integration: `tests/integration/test_billing_meter.py` |
| Billing API router | `app/api/routers/billing.py` | integration: `tests/integration/test_billing_api.py` |
| Bootstrap CLI | `app/cli.py` | integration |
| Worker backoff and Redis-pop helper | `app/worker/worker.py` | unit: `tests/unit/test_worker_backoff.py` |
| Worker processor and reconciliation | `app/worker/processor.py` | integration: `tests/integration/test_worker_processor.py` |
| Worker runtime and module entrypoint | `app/worker/worker.py`, `app/worker/__main__.py` | integration: `tests/integration/test_worker_runtime.py` |
| Frontend dashboard, hooks, and routes | `control-plane/frontend/src/` | fe-unit: Vitest tests beside the source |

**Rule:** a task creating any layer above writes that layer's tests **in the same task** (highest type wins if it spans layers). `Tests: none` only where the matrix says none.

## Gate Check Commands

| Gate | Command | When |
| --- | --- | --- |
| **quick** | `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit` | unit-only tasks; fast inner loop |
| **full** | `ruff check . && ruff format --check . && mypy app/ && pytest -q` (requires `compose.test.yml` up) | any task with integration tests |
| **build** | `python -c "import app.main"` (import smoke) + `alembic upgrade head` on test DB | skeleton / wiring tasks |
| **fe** | `cd control-plane/frontend && npm run lint && npm run typecheck && npm run test -- --run && npm run build` | frontend work; runs lint, TypeScript, Vitest, and the production Vite build |

Every task's **Done when** cites the expected pass count (e.g. "N tests pass") to prevent silent test deletion.

The frontend gate is independent of `compose.test.yml`, so it can run in
parallel with control-plane integration tests when no shared source files are
being edited. Keep frontend component and hook tests close to their source, and
use the typed API client rather than mocking browser fetch behavior in each
panel.

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
- Inject `FakeBlockedPortsWriter` into `BlockedPortReconciler` and worker tests
  to verify desired-state convergence without invoking `dpstat set-blocked-ports`.
- Secrets: tests never assert on plaintext passwords; a log-capture fixture asserts **no** credential material is emitted (Success Criteria).

### Billing conventions

- Use `FakeTelemetryReader` from `app/worker/telemetry_reader.py` for billing-meter
  integration tests. Feed it deterministic telemetry snapshots rather than
  invoking the data-plane reader.
- Calculate p95 with the nearest-rank method: sort the period's `clean_bps`
  samples, select rank `ceil(0.95 * n)`, and return `0` for no samples.
- Convert clean bytes per second to Gbps with `bytes_per_second * 8 /
  1_000_000_000`; the `* 8` converts bytes to bits. Quantize the result to two
  decimal places before comparing it with a plan's committed Gbps.
- Treat billing periods as UTC calendar months, with bounds from the first day
  at `00:00:00Z` through, but excluding, the first day of the next month.
- Refresh only `open` usage rows. The meter finalizes an `open` row when its
  period ends and never changes a `final` row.

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

### Threat feed sync conventions

- **Parser** (`test_feed_parser.py`, unit): table-driven `parametrize` over the
  accept/reject grammar (BOM, comments, `/32` promotion, `/0` and host-bit
  rejects, IPv6 reject, dedup) asserting the `ParseResult` counts and the
  `success`/`partial`/`failed` outcome. Pure logic, no I/O.
- **Fetcher** (`test_feed_fetch.py`, unit): drive `httpx.MockTransport` for
  redirect/non-2xx/oversize-`Content-Length`/streamed-overflow paths and a
  deterministic slow stream for the wall-clock timeout. A log-capture assertion
  proves the credential name, value, bearer header, and body never appear in
  errors or logs.
- **Feed worker** (`test_feed_reconcile.py`, `test_feeds_service.py`,
  `test_worker_feed_jobs.py`, `test_feed_sync_runner.py`, `test_feed_scheduler.py`,
  `test_feed_coordinator.py`, `test_global_deny_applier.py`, integration): use the
  `committed_db` fixture (not the rollback `db_session`) because the runner uses
  short `session_scope` transactions. Inject a recording/failing global-applier
  double so the fetch → parse → reconcile → apply path is exercised without the
  BPF helper. Cover success, partial, each keep-last failure, dry-run, no-op,
  duplicate delivery, applier failure, orphan recovery, and convergence retry.
- **Non-parallel:** all feed integration suites share the single `compose.test.yml`
  Postgres + Redis, so they run serially with the other integration suites — never
  fan out concurrent integration runs.

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

### Per-service telemetry counters and snapshots

`svc_stat_map` is a preallocated per-CPU hash keyed by the control-plane
`dp_id`. Each matched service records exact clean and dropped packet/byte
counters, plus a packet count for every drop reason. Unmatched traffic remains
node-only. The map resets when the XDP program reloads; the worker treats that
as a counter reset and computes deltas rather than lifetime totals.

Run the machine-readable snapshot command while the loader owns the pinned
maps:

```sh
sudo ./build/dpstat snapshot --json
sudo ./build/dpstat snapshot --json --ifindex <ingress-ifindex>
```

The command writes one JSON object to standard output containing the timestamp,
active slot/version, XDP attachment data, node counters, sampling and bloom
statistics, and sorted per-service `dp_id` counter rows. Supplying the ingress
ifindex lets `dpstat` report `native`, `generic`, or `offline` XDP mode. A
missing or unreadable pinned map is an offline/error condition, not a partial
snapshot; consumers must retain the last good window and mark it stale.

The native-mode loader is verified by the build gate plus a manual veth/NIC smoke:
`SERVICE_DEST=<ipv4-or-cidr> sudo ./build/xdp_gateway_loader <in-ifname> <out-ifname>` should attach to
IN in native/DRV mode, populate `tx_devmap[0]` with OUT, pin observability maps under
`/sys/fs/bpf/xdp_gateway/`, seed the demo service when provided, or fail clearly with no generic/SKB
fallback. Ctrl-C should detach cleanly and remove the pins.

### Node bypass and maintenance conventions

The global soft-bypass test seam writes `node_control[0].bypass` directly with
`bpf_map_update_elem` in the dp-unit harness. Cover a bypassed IPv4
`service_miss`, the separate `bypass_counter`, the unchanged per-service
`svc_stat`, and the bypass-off enforcement path. IPv6, malformed IPv4, and
fragments remain fail-fast drops even while bypass is on.

The loader pins `node_control` and `bypass_counter` under
`/sys/fs/bpf/xdp_gateway/`. Use the privileged operator interface only against
a running, pinned gateway:

```sh
sudo ./build/dpstat set-bypass 1
sudo ./build/dpstat snapshot --json
sudo ./build/dpstat set-bypass 0
```

The snapshot contains `node_control.bypass` and exact aggregate
`bypass.pkts`/`bypass.bytes` values. The `smoke_bypass.sh` variant in
`make smoke` verifies an undeclared IPv4 destination redirects unchanged while
bypass is on, then drops after bypass is cleared. Keep this privileged smoke
serial with the other veth tests.

Worker node-control tests use `committed_db` and `FakeBypassWriter` to cover
desired-state convergence, retry after a writer failure, restart reassertion,
and the maintenance-clear reconcile kick. Worker runtime coverage must also
prove that the node-control lane can assert bypass while an apply is blocked.
Maintenance processor tests hold `SERVICE_UPDATE` jobs in `queued` and confirm
that the latest version drains after maintenance clears.

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
blacklist bloom/LPM maps, `gbl_meta`, and the UDP blocked-port bitmap. Use lower-level bloom-only helpers for
deterministic false-positive induction. A bloom hit with an LPM miss must increment exactly one
`bloom_stats` stage counter and must not change drop-reason counters.

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

The wire format is the `apply_snapshot.h` **v4** contract (magic `XDPGWAP1`, `schema_version`, a
`SERVICE_FULL | GLOBAL_DENY` kind, then either the per-service records — service-level VIP and the
`dp_id` surrogate — or the sorted global-deny `{prefixlen, address_be32}` entries with the desired
revision). `make apply` runs `build/test_snapshot` against **both** committed fixtures
(`tests/fixtures/apply_snapshot_golden.bin` and `global_deny_snapshot_golden.bin`); the control-plane
`serialize_node_snapshot` and `serialize_global_snapshot` must emit byte-identical output, so the
fixtures bind the C parser and the Python serializers. Bump `schema_version` in the header and regenerate
the fixtures together on any layout change — readers reject an unknown kind or version before touching a
map.

**Global-deny apply** (`GLOBAL_DENY` mode) is the inverse carry-forward: dp-unit cases rebuild the
feed-owned `global_blacklist_bloom`/`_lpm` and `gbl_meta` from the snapshot while pointer-carrying every
service-scoped outer, `fair_node_config`, and `udp_blocked_port_bitmap`, then replay a
`BPF_PROG_TEST_RUN` packet to confirm a listed source reaches `blacklist_drop` and unrelated verdicts are
unchanged. Cover the `/24` bloom expansion, the `GBL_F_HAS_BROAD` broad-prefix escape, the empty-snapshot
meta clear, build/verify failures that preserve the live slot, and service↔global alternation.

`sudo make applybulk` (service) and `sudo make globalapplyscale` (global) are the scale gates.
`applybulk` asserts a 1000-service build/verify/flip in under 5 s with a single `active_config` flip and
carried-forward feed maps. `globalapplyscale` loads **1,048,576** distinct entries and rejects
**1,048,577** before the flip. `sudo make globalapplysmoke` drives a fake feed snapshot through the real
helper to a `blacklist_drop` XDP verdict. None are part of `make test`; run them when the apply path or
the slotted config-map set changes.

### Data-plane Corpus

`dp-unit` tests use adversarial synthetic frames, including IPv6, unsupported EtherTypes, runt Ethernet,
malformed IPv4, first and later IPv4 fragments, truncated L4 headers, ARP, single VLAN, QinQ, too-deep
VLAN stacks, service lookup verdicts, allow-rule matching, deterministic per-rule rate limits,
whitelist scoped-match and VIP ceiling cases, blacklist amp-port, bogon, bitmap, global
bloom-to-LPM, bloom false-positive, sampling budget, and `xdpgw-apply` build/verify/flip, fresh-inner,
fail-closed rollback, and `GLOBAL_DENY` inverse-carry-forward/alternation cases. The current quick suite
has **136** tests plus the `build/test_snapshot` service+global golden self-tests. Each verdict task
states the expected passing test count to prevent silent deletions or skipped coverage.
