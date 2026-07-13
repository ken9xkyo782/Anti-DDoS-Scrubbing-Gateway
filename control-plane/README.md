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
