# Agent Worker & Job Pipeline Tasks

**Design**: `.specs/features/agent-worker/design.md` (AD-027)
**Spec**: `.specs/features/agent-worker/spec.md` (AGW-01..30)
**Context**: `.specs/features/agent-worker/context.md` (D-AGW-1..2, A-AGW-1..8)
**Status**: **APPROVED** (2026-07-10) → Execute (T1 first). Tools: `coding-guidelines` on T1–T5,
`docs-writer` on T6 (confirmed).

**Baseline** (`control-plane/`, static count — **pin exact `pytest -q` total live at Execute start**):
**B = 209** test functions (29 unit + 180 integration). All new tests are additive; **no pre-existing
test is removed or weakened** (silent-deletion guard on every task).

**Gate commands** (from `.specs/codebase/TESTING.md`, run in `control-plane/`):
- **quick** — `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit`
- **full** — `ruff check . && ruff format --check . && mypy app/ && pytest -q` (requires `compose.test.yml` up)

**Parallelism rule** (TESTING.md): only **unit**-tested tasks may be `[P]`; **integration** tests share
the single `compose.test.yml` PG+Redis and are **not** parallel-safe. This feature is almost entirely
integration → **serial**; only the docs task (`none`) is `[P]`.

**Design flags resolved for Execute** (were the 5 open questions in design.md §Open Questions):
1. **no-handler test** → monkeypatch `HANDLERS` to an empty/other-type map in the test (no enum/schema churn).
2. **config snapshot depth** → build the **full** `ServiceConfig` (service + plan + rules + whitelist +
   blacklist) now, so M4 #2 inherits it; placeholder logs a summary only.
3. **`session_scope` placement** → add to `app/db/session.py` (shared, additive); **do not** refactor
   `get_db` in this feature.
4. **degraded-Redis test** → an **opt-in/gated** integration case (env-flagged, e.g. `WORKER_REDIS_DOWN_TEST=1`);
   if not scriptable in CI, ship as a documented manual check (T6). Never a silent gap.
5. **structured logs** → stdlib `logging` with `extra={...}` fields; JSON formatter deferred to M5.

**Testing-infra note (applies to T4/T5):** the worker's `session_scope` **commits for real**, so the
concurrency/supersede and reconcile tests cannot use the rollback-isolation `db_session` fixture. Use the
**truncation-isolation** variant (TESTING.md sanctions "rolled-back transaction **or** truncated schema").
T4 adds a small `committed_db` / truncate-tables fixture to `tests/integration` (or per-test cleanup) as
its first step; later tasks reuse it.

**Tools** (no MCPs configured — STATE Preferences): Skill `coding-guidelines` on every Python code task;
`docs-writer` on T6. Confirm at approval.

---

## Execution Plan

### Phase 1 — Foundation (sequential; both integration)
```
T1 (session_scope) ──▶ T2 (applier boundary)
```
T1 and T2 touch disjoint files but both carry integration tests (shared infra) → run one at a time.

### Phase 2 — Pipeline core (sequential)
```
T2 ──▶ T3 (handlers) ──▶ T4 (processor: process_job / reconcile_once / recover_orphan)
                          ▲
T1 ───────────────────────┘   (process_job uses session_scope + applier + handlers)
```

### Phase 3 — Runtime & docs
```
T4 ──▶ T5 (Worker runtime + settings + entrypoint) ──▶ T6 (docs) [P]
```

---

## Task Breakdown

### T1: `session_scope` unit-of-work

**What**: Add a shared non-request `session_scope` async context manager that commits then runs
post-commit callbacks (mirrors `get_db`), so the worker's `retry`/`enqueue`-registered re-dispatch fires.
**Where**: `app/db/session.py` (add `session_scope`); `tests/integration/test_db_session.py` (extend).
**Depends on**: None
**Reuses**: `get_session_factory`, `add_post_commit_callback`, `run_post_commit_callbacks`,
`discard_post_commit_callbacks` (all in `app/db/session.py`).
**Requirement**: AGW-04 (infra enabling `retry` re-dispatch), foundation for AGW-12/22.

**Tools**: MCP: none · Skill: `coding-guidelines`

**Done when**:
- [ ] `@asynccontextmanager async def session_scope() -> AsyncIterator[AsyncSession]`: yields a session;
      on success `await session.commit()` then `await run_post_commit_callbacks(session)`; on exception
      `discard_post_commit_callbacks(session)` + `await session.rollback()` + re-raise.
- [ ] `get_db` left **unchanged** (additive only).
- [ ] Integration tests: (a) a registered post-commit callback **runs** after a successful scope;
      (b) an exception inside the scope → rollback + callback **discarded** (does not run) + raised;
      (c) a committed row is visible in a fresh session.
- [ ] Gate passes: `full` · **Test count**: ≥ B+3 (no pre-existing test removed)

**Verify**: `cd control-plane && ruff check . && mypy app/ && pytest -q tests/integration/test_db_session.py`
**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): add session_scope unit-of-work with post-commit dispatch`

---

### T2: Applier boundary + config snapshot

**What**: Create the `Applier` protocol, the frozen `ServiceConfig` snapshot, `PlaceholderApplier`
(v1, logs + succeeds), and `load_service_config` (read full config at apply time).
**Where**: `app/worker/__init__.py` (new package), `app/worker/applier.py`; `tests/integration/test_worker_applier.py`.
**Depends on**: None (models only)
**Reuses**: `ProtectedService` + `plan`/`rules`/`whitelist_entries`/`blacklist_entries` relationships,
`ServicePlan`, `AllowRule`, `WhitelistEntry`, `BlacklistEntry` (`app/db/models.py`).
**Requirement**: AGW-07/09/10/11 (boundary + snapshot); D-AGW-1.

**Tools**: MCP: none · Skill: `coding-guidelines`

**Done when**:
- [ ] `@dataclass(frozen=True) ServiceConfig` carries `service_id`, `version`, service fields
      (name, `cidr_or_ip`, mode, enabled, `vip_pps`, `vip_bps`), `plan`, `rules`, `whitelist`,
      `blacklist` (immutable snapshot — full depth per flag #2).
- [ ] `class Applier(Protocol): async def apply(self, config: ServiceConfig) -> None` (raises on failure).
- [ ] `PlaceholderApplier.apply` logs one structured summary line (service + rule/list counts + version)
      and returns; **touches no bpffs/data-plane** (AGW-21 binding).
- [ ] `async def load_service_config(db, service_id) -> ServiceConfig | None` — eager-loads children;
      returns `None` when the service is absent.
- [ ] Integration tests: snapshot of a seeded service with rules+lists has correct counts/version;
      empty-children service snapshots cleanly; missing service → `None`; `PlaceholderApplier` returns
      without error and logs (caplog).
- [ ] Gate passes: `full` · **Test count**: ≥ B+7 (T1 included)
**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): applier boundary, ServiceConfig snapshot & placeholder applier`

---

### T3: Handler registry + SERVICE_UPDATE handler

**What**: Add the `JobType → handler` registry, `handle_service_update` (config → applier), and
`NoHandlerError`.
**Where**: `app/worker/handlers.py`; `tests/integration/test_worker_handlers.py`.
**Depends on**: T2
**Reuses**: `JobType` (`app/db/models.py`), `load_service_config` + `Applier` (T2).
**Requirement**: AGW-07/08/09/10.

**Tools**: MCP: none · Skill: `coding-guidelines`

**Done when**:
- [ ] `Handler = Callable[[AsyncSession, AgentJob, Applier], Awaitable[None]]`;
      `HANDLERS: dict[JobType, Handler] = {JobType.service_update: handle_service_update}`.
- [ ] `handle_service_update(db, job, applier)`: `cfg = await load_service_config(db, job.target_id)`;
      `cfg is None` → raise (→ mark_failed "service missing"); else `await applier.apply(cfg)`.
- [ ] `class NoHandlerError(Exception)` defined (raised by the processor, not here).
- [ ] Integration tests: `handle_service_update` invokes a `RecordingApplier` with the target's config
      (AGW-09); missing-service handler raises; `HANDLERS[JobType.service_update]` resolves.
- [ ] Gate passes: `full` · **Test count**: ≥ B+10
**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): job-type handler registry & SERVICE_UPDATE handler`

---

### T4: Job processor — process_job / reconcile_once / recover_orphan (crux)

**What**: The two-transaction version-guarded job processor, the ledger reconcile sweep, and orphan
auto-recovery — plus the truncation-isolation test fixture the committing tests need.
**Where**: `app/worker/processor.py`; `tests/integration/conftest.py` (truncate fixture);
`tests/integration/test_worker_processor.py`.
**Depends on**: T1, T2, T3
**Reuses**: `mark_applying`/`mark_active`/`mark_failed`/`retry`/`enqueue_service_update`,
`APPLY_QUEUE_KEY` (`app/services/apply.py`); `session_scope` (T1); `HANDLERS` (T3); `AgentJob`,
`JobStatus`, `ProtectedService` (models); `bump_version` (`app/services/services.py`) for the churn test.
**Requirement**: AGW-01(part)/02/03/07/08/09/10/11/12/13/15/16/17/18/19/20/22/24/30.

**Tools**: MCP: none · Skill: `coding-guidelines`

**Done when**:
- [ ] `process_job(job_id, *, session_factory, applier)`: **Txn1** `session_scope` — `db.get(AgentJob)`
      None → log+skip (AGW-16); `mark_applying`; re-read `job.status`; commit. `proceed = status is
      applying` else return (AGW-17/18). **Handler** (missing type → `NoHandlerError` path). **Txn2**
      `session_scope` — success → `mark_active`; handler raised → `mark_failed(f"{type}: {e}")`
      (`logger.exception` full trace). Handler/applier exceptions → `mark_failed`; **infra** exceptions
      propagate (not converted to `mark_failed`), with a bounded terminal-mark retry (AGW-11/15/25).
- [ ] `reconcile_once(*, session_factory, applier, include_orphans)`: process `queued` jobs
      oldest-`version`-first via `process_job`; if `include_orphans`, `recover_orphan` each `applying`
      job; return count (AGW-12/13).
- [ ] `recover_orphan(db, job)` (inside `session_scope`): `mark_failed(job.id, "worker restarted
      mid-apply")` then `retry(db, service, actor=None)` — one txn, existing edges (AGW-22, D-AGW-2).
- [ ] Truncation-isolation fixture added (real-commit tests clean up deterministically).
- [ ] Integration tests (all AGW-cited):
      **happy ≤5 s** enqueue→`process_job`→`active`, `active_version=N`, elapsed ≤5 s (AGW-05 part);
      **superseded-skip** claim on an already-superseded job → handler skipped (AGW-18);
      **no-handler** (monkeypatched `HANDLERS`) → `failed`, no crash (AGW-08);
      **handler-fail** `FailingApplier` → `failed`, `active_version` unchanged (AGW-11), then service
      `retry` + fixed applier → `active` (AGW-30);
      **reconcile / Redis-lost** committed `queued` job `dispatched_at IS NULL` → `reconcile_once` →
      `active` (AGW-12/13);
      **orphan recovery** seeded `applying` → `reconcile_once(include_orphans=True)` → re-queued,
      `apply.retry` (system) audit row present, `attempts` incremented, eventually `active` (AGW-22/24);
      **supersede-under-churn (crux)** `BarrierApplier` blocks after `mark_applying`; commit
      `bump_version→N+1` in a separate session; release → vN `mark_active` no-ops (`superseded`),
      `process_job(vN+1)` → `active_version=N+1`, exactly one advance (AGW-18/19/20);
      **duplicate delivery** `process_job(id)` twice → one advance, second no-op (AGW-17).
- [ ] Gate passes: `full` · **Test count**: ≥ B+20
**Tests**: integration · **Gate**: full
**Commit**: `feat(worker): version-guarded job processor, ledger reconcile & orphan recovery`

---

### T5: Worker runtime + settings knobs + entrypoint

**What**: The long-running `Worker` (BRPOP loop, startup/periodic reconcile, signal-based graceful
shutdown, Redis/DB bounded-backoff degrade), the `CONTROL_PLANE_WORKER_*` settings, and the
`python -m app.worker` entrypoint.
**Where**: `app/worker/worker.py`, `app/worker/__main__.py`, `app/core/config.py` (add `WORKER_*` fields);
`tests/integration/test_worker_runtime.py`; `tests/unit/test_worker_backoff.py`.
**Depends on**: T4
**Reuses**: `get_redis_client`/`close_redis_client` (`app/core/redis.py`), `get_session_factory`/
`dispose_engine` (`app/db/session.py`), `process_job`/`reconcile_once` (T4), `Settings`/`get_settings`
(`app/core/config.py`), `app/cli.py` entrypoint shape.
**Requirement**: AGW-01/05/06/13/14/21/23/26/28/29.

**Tools**: MCP: none · Skill: `coding-guidelines`

**Done when**:
- [ ] `Settings` gains `worker_poll_timeout_seconds=2.0`, `worker_reconcile_interval_seconds=15.0`,
      `worker_backoff_initial_seconds=0.5`, `worker_backoff_max_seconds=30.0`,
      `worker_shutdown_grace_seconds=10.0` (env `CONTROL_PLANE_WORKER_*`).
- [ ] `Worker` (ctor injects settings/redis/session_factory/applier): `run(stop=None)` — install
      SIGTERM/SIGINT → stop event (AGW-23); startup `reconcile_once(include_orphans=True)` + log effective
      config once (AGW-21/26); loop `BRPOP(key, poll)` → `(key,value)`→UUID→`process_job` (bad UUID →
      log+skip); `None`→reconcile-if-due `include_orphans=False` (AGW-06/13); `RedisError` → degraded
      DB-poll on backoff, retry BRPOP, resume (AGW-14); DB `OperationalError` → bounded backoff, no work
      dropped (AGW-15); shutdown → in-flight job `wait_for(grace)`, then `close_redis_client` +
      `dispose_engine` (AGW-23).
- [ ] `_brpop` wrapper returns `uuid.UUID | None` (isolates the verified redis-py contract).
- [ ] `app/worker/__main__.py`: configure logging; `Worker(settings=get_settings(),
      applier=PlaceholderApplier()).run()` under `asyncio.run` (AGW-28).
- [ ] Unit test (`[P]`-eligible, in `tests/unit`): backoff sequence initial→max capped; `_brpop` maps
      `(key,value)`→UUID and `None`→None (pure, redis stubbed).
- [ ] Integration tests: `Worker.run(stop)` with `stop` set after one enqueue → job `active` ≤5 s
      (AGW-01/05); graceful shutdown exits cleanly, mid-flight job (forced stop) left `applying` and
      healed by a follow-up startup sweep (AGW-23/24); **[gated]** `WORKER_REDIS_DOWN_TEST` degrade case
      (flag #4) or documented manual check deferred to T6.
- [ ] Gate passes: `full` · **Test count**: ≥ B+25 (unit +2, integration +3)
**Tests**: integration (+ unit for backoff) · **Gate**: full
**Commit**: `feat(worker): worker runtime loop, settings knobs & python -m app.worker entrypoint`

---

### T6: Docs — worker section & run command [P]

**What**: Document the worker in `TESTING.md` (worker test conventions: truncation isolation, injectable
appliers, gated degrade check) and `README`/run docs (`python -m app.worker`, env knobs, D-AGW-1
"active = acknowledged by worker until M4 #2").
**Where**: `.specs/codebase/TESTING.md`, `control-plane/README.md` (or the repo run docs).
**Depends on**: T5
**Reuses**: existing TESTING.md structure; data-plane feature docs precedent.
**Requirement**: AGW-25/26/28/29 (operational documentation), D-AGW-1 caveat.

**Tools**: MCP: none · Skill: `docs-writer`

**Done when**:
- [ ] TESTING.md: worker rows added (unit `test_worker_backoff`, integration `test_worker_*`), truncation
      fixture + injectable-applier convention, gated Redis-down note.
- [ ] Run docs: `python -m app.worker` command, `CONTROL_PLANE_WORKER_*` env table with defaults,
      colocation note (A-AGW-1), and the D-AGW-1 placeholder caveat.
- [ ] No code change; `full` gate still green (docs-only).
- [ ] Gate passes: `full` (unchanged count) · **Test count**: = T5's total
**Tests**: none · **Gate**: full (no-op verification)
**Commit**: `docs(worker): worker run command, env knobs & test conventions`

---

## Pre-Approval Validation

### Check 1 — Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | 1 fn (`session_scope`) + its tests | ✅ Granular |
| T2 | 1 module (applier boundary: protocol + dataclass + placeholder + loader — cohesive) | ✅ Granular |
| T3 | 1 module (registry + one handler) | ✅ Granular |
| T4 | 1 module (processor: 3 cohesive coroutines) + test fixture | ✅ Granular |
| T5 | 1 runtime module + entrypoint + its settings (cohesive "runnable worker") | ✅ Granular |
| T6 | docs only | ✅ Granular |

### Check 2 — Diagram / Definition Cross-Check

| Task | Depends on (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (root) | ✅ |
| T2 | None | (root) | ✅ |
| T3 | T2 | T2→T3 | ✅ |
| T4 | T1, T2, T3 | T1→T4, T3→T4 (T2→T3→T4 chain) | ✅ |
| T5 | T4 | T4→T5 | ✅ |
| T6 | T5 | T5→T6 | ✅ |

No `[P]` task depends on another in its phase (T6 is the only `[P]`, alone in Phase 3 tail).

### Check 3 — Test Co-location

| Task | Code layer created/modified | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1 | `app/db/session.py` (DB session) | integration | integration | ✅ |
| T2 | `app/worker/applier.py` (reads DB) | integration | integration | ✅ |
| T3 | `app/worker/handlers.py` (reads DB) | integration | integration | ✅ |
| T4 | `app/worker/processor.py` (DB+Redis) | integration | integration | ✅ |
| T5 | `app/worker/worker.py`+`config.py` (DB+Redis) | integration | integration (+unit backoff) | ✅ |
| T6 | docs | none | none | ✅ |

All ✅ — no violations. Only T6 (`none`) is `[P]`; every integration task is serial (shared infra).

---

## Traceability (all 30 requirements mapped)

| Req | Task | Req | Task | Req | Task |
| --- | --- | --- | --- | --- | --- |
| AGW-01 | T5 (loop) / T4 | AGW-11 | T4 | AGW-21 | T5 |
| AGW-02 | T4 | AGW-12 | T4 | AGW-22 | T4 |
| AGW-03 | T4 | AGW-13 | T4/T5 | AGW-23 | T5 |
| AGW-04 | T1/T4 | AGW-14 | T5 | AGW-24 | T4/T5 |
| AGW-05 | T4/T5 | AGW-15 | T4 | AGW-25 | T4/T5 |
| AGW-06 | T5 | AGW-16 | T4 | AGW-26 | T5 |
| AGW-07 | T3 | AGW-17 | T4 | AGW-27 | (M1 `GET /jobs`, no code) |
| AGW-08 | T3/T4 | AGW-18 | T4 | AGW-28 | T5 |
| AGW-09 | T2/T3 | AGW-19 | T4 | AGW-29 | T5 |
| AGW-10 | T2/T3 | AGW-20 | T4 | AGW-30 | T4 |

**AGW-27** needs no code — M1's admin `GET /jobs` already reflects worker state; T6 documents it.

---

## Notes for Execute

- **Pin B live** with `pytest -q` (compose.test.yml up) before T1; every task's count is `≥ B + Δ` and
  must show **no** pre-existing test removed.
- **`apply.py` stays byte-for-byte unmodified** (AGW-04). If any task feels it needs to change `apply.py`,
  stop — the boundary is wrong.
- **Real-commit isolation**: T4 onward use the truncation fixture, not the rollback `db_session`.
- **First fail-fast moment**: T4's supersede-under-churn test is the proof that the two-transaction lock
  release actually enables supersession — run it early within T4.
