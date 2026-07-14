# Control-plane worker

## Configure the worker

Configure the control plane before starting the worker. Set
`CONTROL_PLANE_DATABASE_URL` and `CONTROL_PLANE_REDIS_URL` for the gateway node.
The worker uses the same database and Redis configuration as the control plane. It
also reads `CONTROL_PLANE_*` settings from the environment and the `.env` file in
the current working directory. Apply the control-plane database migrations before
running the worker.

## Run the worker

From `control-plane/`, run:

```sh
python -m app.worker
```

The worker logs its effective configuration at startup, reconciles the database
ledger, then waits for jobs on the Redis `apply:jobs` queue.

## Configure worker timing

All worker timing values are positive seconds.

| Environment variable | Default | Behavior |
| --- | --- | --- |
| `CONTROL_PLANE_WORKER_POLL_TIMEOUT_SECONDS` | `2.0` | Sets each Redis BRPOP wait for `apply:jobs`; a timeout lets the worker check for scheduled reconciliation. |
| `CONTROL_PLANE_WORKER_RECONCILE_INTERVAL_SECONDS` | `15.0` | Sets the interval between queued-ledger reconciliation sweeps after idle Redis polls. Startup always runs a sweep, and Redis degradation also uses ledger reconciliation. |
| `CONTROL_PLANE_WORKER_BACKOFF_INITIAL_SECONDS` | `0.5` | Sets the first retry delay after Redis or database failures. |
| `CONTROL_PLANE_WORKER_BACKOFF_MAX_SECONDS` | `30.0` | Caps the doubling retry delay after Redis or database failures. |
| `CONTROL_PLANE_WORKER_SHUTDOWN_GRACE_SECONDS` | `10.0` | Sets how long shutdown waits for the in-flight job. After the grace period, the job remains `applying` for a later startup sweep to recover. |

## Telemetry lane and dashboards

The worker runs telemetry as an independent background lane, not as an
`AgentJob`. It invokes the data-plane `dpstat snapshot --json` reader on a
whole-second cadence, stores windowed service and node telemetry, and keeps job
processing available if a telemetry read fails. The first successful reader
snapshot establishes a zero-delta baseline. If the gateway reader is offline,
the lane stores an `offline` node-health snapshot and resumes on the next tick.

Configure the lane with these environment variables:

| Environment variable | Default | Behavior |
| --- | --- | --- |
| `CONTROL_PLANE_WORKER_TELEMETRY_ENABLED` | `true` | Enables the telemetry background lane. |
| `CONTROL_PLANE_WORKER_TELEMETRY_INTERVAL_SECONDS` | `2` | Exact aggregation window in seconds. Values must be `1` or `2`. |
| `CONTROL_PLANE_WORKER_TELEMETRY_RETENTION_SECONDS` | `604800` | Retains seven days of telemetry and health windows. |
| `CONTROL_PLANE_WORKER_TELEMETRY_BINARY_PATH` | `../data-plane/build/dpstat` | Path to the `dpstat` executable. |
| `CONTROL_PLANE_WORKER_TELEMETRY_IFINDEX` | unset | Optional ingress ifindex for live XDP-mode detection. |
| `CONTROL_PLANE_WORKER_TELEMETRY_TIMEOUT_SECONDS` | `5.0` | Bounds each reader subprocess. |

Build the data-plane reader and start the worker from `control-plane/`:

```sh
cd ../data-plane && make dpstat
cd ../control-plane && python -m app.worker
```

The data-plane loader must be running and must own the pinned observability
maps. See the data-plane README for the `dpstat snapshot --json` contract.

## Billing metering and usage

The worker runs billing metering as an independent background lane. It reuses
the telemetry reader's `dpstat` binary path, optional ingress ifindex, and
subprocess timeout. The lane takes coarse per-service samples, refreshes the
current billing-period rollup, finalizes due periods, and prunes old samples.

Configure the lane with these environment variables:

| Environment variable | Default | Behavior |
| --- | --- | --- |
| `CONTROL_PLANE_WORKER_BILLING_ENABLED` | `true` | Enables the billing background lane. |
| `CONTROL_PLANE_WORKER_BILLING_INTERVAL_SECONDS` | `300.0` | Sets the interval between billing-meter iterations. |
| `CONTROL_PLANE_WORKER_BILLING_SAMPLE_RETENTION_DAYS` | `400` | Prunes samples only after their finalized billing period reaches this age. |
| `CONTROL_PLANE_WORKER_BILLING_PERIOD` | `monthly` | Selects the only supported billing period. |

Billing periods are UTC calendar months. A period begins at `00:00:00Z` on the
first day and ends, exclusively, at the first day of the following month. The
meter calculates p95 with the nearest-rank method over clean-byte-per-second
samples. It converts that value to Gbps by multiplying by `8` before dividing
by `1_000_000_000`, then rounds to two decimal places. The resulting `open`
usage is provisional; when the period ends, the worker marks it `final` and no
longer updates it.

### Billing APIs and exports

Authenticated users can call `GET /billing/usage` for current or finalized
usage. You can filter by `service_id`, `period` (`YYYY-MM`), and `status`
(`open` or `final`). Tenant users see only their tenant's usage and can query
only services they own. Administrators can retrieve all tenant usage or filter
it with `tenant_id`.

`GET /billing/usage/history` returns finalized usage only, newest first. It
accepts an optional `service_id` and a `limit` from `1` through `100` (default
`12`), with the same tenant service scope.

Administrators export a finalized calendar month with
`GET /billing/usage/export?period=YYYY-MM&format=csv` or `format=json`. The
endpoint excludes provisional `open` usage for both formats.

## Telemetry APIs and SPA deployment

Tenant users can read `GET /services/{service_id}/telemetry` only for services
they own. Administrators can read `GET /node/telemetry` and `GET /node/health`.
The node-health response includes live queued/applying job counts and current
feed-source status. All responses include a `has_data` marker, UTC window
metadata, and `stale`; a no-data response is zeroed, has a null window start,
uses `window_seconds: 0`, and is stale.

Build the React application and set its output directory to let FastAPI serve
the SPA:

```sh
cd control-plane/frontend && npm run build
export CONTROL_PLANE_FRONTEND_STATIC_DIR="$PWD/dist"
```

With this setting, FastAPI serves the built assets and returns `index.html` for
browser history routes such as `/tenant` and `/admin`. API prefixes and missing
assets still return their normal 404 responses; the fallback never returns SPA
HTML for them. Leave the setting unset when another web server serves the
frontend.

## Deploy and observe

Run one dedicated worker process on the gateway node. It uses the same database
and Redis configuration as the control plane and processes one job at a time.

Use the administrator endpoint `GET /jobs?status=` to view worker ledger state
and filter jobs by status.

## Configure the apply helper

The worker execs the `xdpgw-apply` data-plane helper to build and swap BPF maps.

| Environment variable | Default | Behavior |
| --- | --- | --- |
| `CONTROL_PLANE_WORKER_APPLY_BINARY_PATH` | `../data-plane/build/xdpgw-apply` | Path to the `xdpgw-apply` helper the worker execs for each apply. |
| `CONTROL_PLANE_WORKER_APPLY_TIMEOUT_SECONDS` | `5.0` | Caps each helper run; a timeout kills the helper and fails the job, leaving the last-good active slot live. |

## Double-buffer applier

The worker applies each committed job with `DoubleBufferApplier`. It loads one consistent,
repeatable-read snapshot of every enabled service, serializes it to the `apply_snapshot` wire format in a
private temporary file, and execs `xdpgw-apply` with the configured timeout. The helper builds the
inactive slot, verifies it, and performs a single `active_config` flip; the worker itself does no BPF
work.

Exit `0` means the config was built, verified, and swapped into the XDP hot path, so the job moves to
`active`. A non-zero exit or a timeout raises `ApplyError` and fails the job, leaving the last-good active
slot live. An `active` state now means the config actually reached the data plane — not merely that the
worker acknowledged the job.

## Threat feed sync

Admins manage threat-intelligence feeds through the admin-only `/feeds` API
(`POST/GET/PUT/DELETE /feeds`, `POST /feeds/{id}/sync`, `GET /feeds/{id}/syncs`).
A feed is the authoritative writer of `source=feed`, `scope=global` blacklist
entries. Feed and manual assertions share one materialized global-deny row per
CIDR; adding a manual entry over a feed row promotes it (feed assertions are
preserved), and removing a manual entry demotes it back to a feed row while any
feed still asserts the CIDR.

### Feed format (plain IPv4/CIDR line list)

Feeds are fetched as plain-text line lists. The parser is UTF-8 (an optional BOM
is accepted) and, per line:

- accepts a bare IPv4 address (normalized to `/32`) or a strict, canonical IPv4
  CIDR;
- ignores blank lines, surrounding whitespace, and `#` or `;` comments
  (full-line or inline);
- **rejects** invalid UTF-8 (whole feed fails), IPv6, `0.0.0.0/0`, malformed
  values, and CIDRs with host bits set. Other reserved/bogon ranges are **not**
  rejected — a feed may legitimately assert bogons.

Exact duplicate CIDRs collapse; a containing and a contained CIDR both remain.
A run records physical line count, valid-distinct, invalid, and duplicate counts.
A response with **zero** valid CIDRs is a `failed` run (an empty `200` is a
failure); a response mixing valid and invalid lines is a `partial` run that
applies the valid subset.

### Fetch limits (credential-safe)

The fetcher uses TLS verification, does **not** follow redirects, ignores
ambient proxy/env config, and streams the decoded body against a hard size cap.
Oversized `Content-Length` fails before any body is read; runaway streams abort
at the cap. All limits are positive-bounded settings:

| Environment variable | Default | Behavior |
| --- | --- | --- |
| `CONTROL_PLANE_FEED_FETCH_CONNECT_TIMEOUT_SECONDS` | `5.0` | TCP/TLS connect timeout. |
| `CONTROL_PLANE_FEED_FETCH_READ_TIMEOUT_SECONDS` | `10.0` | Read-inactivity timeout. |
| `CONTROL_PLANE_FEED_FETCH_WRITE_TIMEOUT_SECONDS` | `5.0` | Write timeout. |
| `CONTROL_PLANE_FEED_FETCH_POOL_TIMEOUT_SECONDS` | `5.0` | Connection-pool acquire timeout. |
| `CONTROL_PLANE_FEED_FETCH_WALL_TIMEOUT_SECONDS` | `30.0` | Whole-fetch wall-clock cap. |
| `CONTROL_PLANE_FEED_FETCH_MAX_DECODED_BODY_BYTES` | `33554432` | 32 MiB decoded-body cap. |

The sync interval per source is validated to `300..604800` seconds (5 minutes to
7 days).

### Credentials by environment reference

A source never stores a secret. It stores an optional `credential_env_var` — an
uppercase environment-variable **name** (regex `^[A-Z][A-Z0-9_]{0,127}$`). At
fetch time the worker resolves that variable and sends `Authorization: Bearer
<value>`; a missing variable fails the run before the request. The name and value
never appear in API responses, audit metadata, run errors, or logs — responses
expose only `has_credential`. Error strings are scrubbed of bearer tokens, URL
userinfo, and `key=value`/`key: value` secrets.

### Disabled, manual, dry-run

- **Disabled** sources are skipped by the scheduler and keep their existing
  assertions; a manual sync is still allowed while disabled.
- **Manual** sync (`POST /feeds/{id}/sync`) enqueues immediately and returns
  `202` with run and job status.
- **Dry run** (`POST /feeds/{id}/sync?dry_run=true`) fetches, parses, and reports
  fetch/parse/overlap stats but mutates **no** assertion, blacklist, desired
  state, or data-plane state.

### Scheduling

An in-worker due-time scheduler enqueues each enabled, non-deleted source whose
`next_sync_at <= now`, one job at a time — a source already queued or running is
never double-enqueued. On terminal success/partial/failure the next run is
scheduled at `finished_at + sync_interval`. Creating/re-enabling a source, or
changing its URL or credential, makes it due immediately; an interval-only change
recomputes the due time. Persisted due sources are caught up on worker startup.

### Whitelist overlap (flag, never remove)

Reconciliation records every feed CIDR that overlaps a whitelist entry as a
`FeedSyncOverlap` row and writes one bounded, credential-free `feed.sync.overlap`
audit summary. Overlaps are **flagged, never removed** — the global-deny set is
authoritative and the whitelist still takes precedence in the data-plane pipeline.

### Deletion and failure recovery

Deleting a source records a dangerous-action audit, hides it behind a tombstone,
removes only its own assertions, and enqueues a convergence run so the global-deny
set and data plane catch up. Per-source isolation is strict: a fetch/timeout/
oversize/non-2xx/zero-valid failure keeps the **last good** assertions and the
current desired/active data-plane version, and never affects another source. A
worker restart mid-apply re-queues the same feed run within its attempt budget
(bounded orphan recovery), and a desired-vs-active global digest divergence is
retried by a `GLOBAL_DENY_APPLY` convergence job without re-fetching the feed.
