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

## Placeholder applier caveat

In v1, `PlaceholderApplier` only logs and succeeds. An `active` state means the
worker acknowledged the job; it does not mean an XDP map was built or swapped.
That meaning changes only when M4 #2 replaces `PlaceholderApplier`.
