# Worker Double-Buffer Apply Flow
> Full-node PostgreSQL snapshot to `xdpgw-apply`

Entry: `control-plane/app/worker/__main__.py:_run_worker()`
Flow: `control-plane/app/worker/handlers.py:handle_service_update()` →
`control-plane/app/worker/applier.py:DoubleBufferApplier.apply()` →
`control-plane/app/worker/processor.py:process_job()` terminal mapping.

## Pointers

- DI/settings: `control-plane/app/worker/__main__.py:_run_worker()`, `control-plane/app/core/config.py:Settings`
- Snapshot read/serialize/helper boundary: `control-plane/app/worker/applier.py:load_node_config()`, `serialize_node_snapshot()`, `DoubleBufferApplier.apply()`
- Wire authority: `data-plane/src/apply_snapshot.h`, `data-plane/tests/fixtures/apply_snapshot_golden.bin`
- Worker ledger guard: `control-plane/app/services/apply.py:mark_active()`, `mark_failed()`
- Coverage: `control-plane/tests/unit/test_snapshot_serialize.py`, `control-plane/tests/integration/test_double_buffer_applier.py`

## Notes

- `DoubleBufferApplier.apply()` starts one repeatable-read, read-only PostgreSQL transaction; only enabled services and their plan/rules/lists enter the node snapshot.
- Snapshot file is 0600, passed as the helper's sole argv input, and unlinked in `finally`; helper nonzero or timeout raises `ApplyError` for the existing processor failure path.
- Construction in `_run_worker()` does not execute a swap; jobs are the only path to `apply()`.
- T2 golden fixture contains one service-blacklist source with `bl_flags=0`; CP has no independent service-blacklist activation field, so serializer follows the fixture. Confirm helper-side semantics when applying such entries.

Updated: 2026-07-13
