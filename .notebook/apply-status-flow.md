# Apply-status flow

Tags: control-plane, apply-status, redis, postgres, fastapi

## Pointers

- Guard: `control-plane/app/core/applystate.py`
- Ledger model: `control-plane/app/db/models.py:AgentJob`
- Dispatch hook: `control-plane/app/db/session.py:add_post_commit_callback()`
- Redis lifecycle: `control-plane/app/core/redis.py`
- Apply service: `control-plane/app/services/apply.py`
- Mutation wiring: `control-plane/app/services/services.py:bump_version()`, `control-plane/app/services/rules.py`, `control-plane/app/services/lists.py`
- API surface: `control-plane/app/api/routers/apply_status.py`

## Notes

- Config mutations write `AgentJob` in the same SQL transaction as the service version/status update.
- Redis `LPUSH apply:jobs` is registered as a post-commit callback, so rolled-back mutations do not dispatch.
- Redis dispatch is best-effort; the DB row is the durable queue of record and `dispatched_at IS NULL` marks recoverable work.
- Worker-facing transitions use the same `protected_service` row lock as `bump_version()` and drop stale job versions by marking them `superseded`.
