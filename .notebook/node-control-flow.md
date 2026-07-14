# Node-control flow
> Global bypass and maintenance controls

Entry: `control-plane/app/api/routers/telemetry.py:set_node_bypass()` (L112)

Bypass: API → `services/node_control.py:set_bypass()` → persisted `NodeControl`
→ `worker/node_control_reconciler.py:NodeControlReconciler.reconcile_once()`
→ `DpstatBypassWriter` → `dpstat set-bypass` → pinned BPF `node_control[0]`
→ `data-plane/src/xdp_gateway.bpf.c` (L220) → `redirect_out_bypass()`.

Accounting: `data-plane/src/node_control.h:bypass_counter` is exact node-global
per-CPU packets/bytes; bypass redirects do not increment `svc_stat` clean
counters. Snapshot contract: `node_control.bypass`, `bypass.pkts`,
`bypass.bytes`.

Maintenance: `set_node_maintenance()` → persisted state →
`worker/processor.py:claim_job()` (L28) leaves `SERVICE_UPDATE` jobs queued.
`NodeControlReconciler` detects the clear edge and wakes worker reconciliation.

Restart: worker starts the node-control lane in `worker/worker.py` (L99); a new
reconciler has no asserted state, so it reasserts persisted bypass on its first
tick.

Updated: 2026-07-14
