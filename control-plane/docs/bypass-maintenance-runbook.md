# Bypass and maintenance OLA runbook

Use this runbook when an administrator needs to stop policy enforcement during
an incident or stage configuration during a maintenance window. Both controls
are node-global and administrator-only. Every state change is audited.

## Before you change state

1. Record the incident or maintenance ticket and the intended operator.
2. Read `GET /node/health` and record the desired and effective bypass and
   maintenance states, the XDP mode, active slot, map version, and bypass
   counters.
3. Confirm the gateway reports a native XDP attachment. If the reader is
   offline or desired and effective bypass differ, treat the control as
   unconfirmed and investigate the worker and pinned data-plane maps.

## Engage emergency bypass

1. As an administrator, call `POST /node/bypass` with `enabled: true` and a
   concise incident reason.
2. Poll `GET /node/health` until `bypass.desired` and `bypass.effective` are
   both `true`. The worker normally asserts the BPF map within one configured
   node-control interval.
3. Confirm the data-plane state when shell access is available:

   ```sh
   sudo ./build/dpstat snapshot --json
   ```

   Check `node_control.bypass` and the separately counted
   `bypass.pkts`/`bypass.bytes` values.

4. Monitor connectivity and record the timestamp, reason, effective state,
   and counter values in the incident record.

Soft bypass forwards parsed IPv4 traffic through the existing header-preserving
redirect and bypasses service policy. ARP continues to redirect normally.
IPv6, malformed IPv4, and fragments still drop; this is not a device-level
link bypass.

## Use maintenance mode

1. As an administrator, call `POST /node/maintenance` with `enabled: true`.
2. Confirm `maintenance.desired` is `true`. Service changes remain accepted
   and queued, but the worker does not dispatch their apply jobs.
3. Stage and review the required configuration changes. Check the job backlog
   through `GET /node/health` or the jobs API.
4. Keep bypass independent. You can engage or clear bypass during maintenance;
   maintenance never blocks the emergency bypass control channel.

## Exit safely

1. Verify the reason for bypass or maintenance is resolved and record a final
   health snapshot.
2. If bypass is active, call `POST /node/bypass` with `enabled: false`.
   Confirm both desired and effective bypass become `false` before relying on
   policy enforcement.
3. If maintenance is active, call `POST /node/maintenance` with
   `enabled: false`. Confirm the queued backlog starts to drain and that the
   latest intended configuration reaches `active`.
4. Record the final active slot, map version, bypass counters, and audit-event
   identifiers. Escalate if desired and effective state remain different.

## Accounting and restart behavior

Bypassed packets and bytes are counted node-wide in `bypass_counter`, not in
per-service clean counters. Treat them as separately accounted operational
traffic and exclude them from scrubbed-clean chargeback reconciliation.

Desired bypass and maintenance state persist in the control-plane database. On
worker restart, the node-control lane reasserts the desired bypass state and
the maintenance dispatch gate remains active. After any restart, confirm the
desired and effective states again before ending the incident or maintenance
window.
