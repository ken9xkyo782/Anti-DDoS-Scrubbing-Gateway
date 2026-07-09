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
- Secrets: tests never assert on plaintext passwords; a log-capture fixture asserts **no** credential material is emitted (Success Criteria).

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
| **build** | `make bpf skel loader dpstat` | Scaffold, loader, observability tooling, and wiring tasks |
| **quick** | `make test` | Parser/verdict tasks with `dp-unit` coverage |
| **full** | `make test && sudo make smoke` | Pre-merge checks on a BPF+veth-capable runner; `make smoke` is privileged and not parallel-safe |

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

### Data-plane Corpus

`dp-unit` tests use adversarial synthetic frames, including IPv6, unsupported EtherTypes, runt Ethernet,
malformed IPv4, first and later IPv4 fragments, truncated L4 headers, ARP, single VLAN, QinQ, too-deep
VLAN stacks, service lookup verdicts, allow-rule matching, deterministic per-rule rate limits,
whitelist scoped-match and VIP ceiling cases, and sampling budget cases. The current quick suite has
**68**
tests. Each verdict task states the expected passing test count to prevent silent deletions or skipped
coverage.
