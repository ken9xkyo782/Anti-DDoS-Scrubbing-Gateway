# Service-Level Rate-Limit Specification

**Milestone:** M3 — Policy enforcement & fairness (refactor of the ARL rate-limit dimension)
**Category ID:** SVR
**Status:** Spec + context complete → Design (3 gray areas resolved via AskUserQuestion: D-SVR-1..3)
**Discuss context:** `.specs/features/service-ratelimit/context.md` (D-SVR-1..3)

**Motivation:** the per-rule `pps`/`bps` rate-limit shipped in ARL is **not wired through the M4 apply
path** — the v2 apply wire format (`apply_snapshot.h`, `APPLY_SNAPSHOT_RULE_SIZE == 10`) carries only
`ports/proto/flags` per rule, never the pps/bps values. Setting the rule's `RULE_F_PPS_SET`/`_BPS_SET`
flags therefore seeded a **zero-token bucket** and dropped 100% of a rule's traffic as `rate_limit_drop`
(root-caused live 2026-07-20 on `118.107.78.137:2283`; hotfix commit `49b7c91` made `_rule_flags` emit
`RULE_F_ENABLED` only, leaving per-rule rate-limit a **no-op**). This feature removes the dead per-rule
knob and replaces it with a **single aggregate rate-limit per service**, correctly plumbed end-to-end.

**Depends on (contractual, mostly reuse):**
- **Whitelist/VIP & VIP ceiling** (`.specs/features/whitelist-vip/`, VERIFIED) — provides the exact
  pattern to mirror: `struct vip_config` (slotted `vip_config_map`) + `vip_ceiling_state` (unslotted
  per-CPU `rl_bucket`) + `vip_bucket_{reset,refill,consume,admit}` reusing `rl_burst`. The new service
  rate-limit is a near-clone on the **clean (non-VIP) path**.
- **Allow-rule matching & rate-limit** (`.specs/features/allow-rule-ratelimit/`, VERIFIED) — this feature
  **amends** ARL: the rule loop keeps first-match-by-priority + `not_allowed`, but the per-rule token
  bucket (`rate_limit_state`, `rl_bucket_admit/consume`, `rule_entry.pps/bps`) is removed; the matched
  seam now calls the service bucket instead.
- **Fairness & bandwidth reservation** (`.specs/features/fairness-bandwidth/`, VERIFIED) — the service
  rate-limit sits **immediately before** the fairness ladder at the `admit_clean` seam; the ladder
  (committed/burst/node from `ServicePlan`) is unchanged.
- **Double-buffer swap / `xdpgw-apply`** (M4 #2, `.specs/features/double-buffer-swap/`) — owns the apply
  wire format + slotted-map rebuild/flip; this feature bumps `APPLY_SNAPSHOT_SCHEMA_VERSION 2→3` and adds
  a slotted `svc_rl_config_map` to the rebuild set.
- **Service, rule & list management (API)** (`.specs/features/service-rule-list/`) — owns `allow_rule`
  and `ProtectedService` shape; this feature drops `allow_rule.pps/bps` and adds
  `ProtectedService.service_pps/service_bps` (mirrors the existing `vip_pps/vip_bps` columns).
- **Config-management SPA** (`.specs/features/config-management-spa/`) — service form gains the
  rate-limit fields; the rule form loses pps/bps.

## Problem Statement

Two things are wrong today. (1) The allow-rule model exposes `pps`/`bps` that **cannot be enforced** —
they are silently ignored (post-hotfix) or, before the hotfix, black-holed the rule. (2) Even if wired,
per-rule buckets are the wrong granularity for an operator who thinks in "this service may receive at
most N pps / M bps". This feature makes rate-limiting a **single, correctly-enforced, per-service
aggregate** and removes the misleading per-rule knob entirely.

## Goals

- [ ] **SVR-01** — Allow rules are pure matchers: `protocol` + optional src/dst port ranges + `enabled`
      + `priority`. `pps`/`bps` are removed from the `allow_rule` table, API, and the data-plane
      `struct rule_entry`. First-match-by-priority and `not_allowed` semantics are unchanged.
- [ ] **SVR-02** — `ProtectedService` gains nullable `service_pps` (packets/s) and `service_bps`
      (bytes/s) — a single aggregate rate-limit config per service (mirrors `vip_pps`/`vip_bps`).
- [ ] **SVR-03** — The data plane enforces the service rate-limit as an **aggregate, per-service,
      per-CPU token bucket** on the **clean (non-VIP) allowed path**: after a rule matches, before the
      fairness ladder (the `admit_clean` seam). Over-limit → `XDP_DROP`, reason `rate_limit_drop`,
      terminal.
- [ ] **SVR-04** — NULL/unset dimension = **unlimited** for that dimension; both unset = the service has
      **no rate-limit** (packets pass straight to the fairness ladder). Matches VIP NULL semantics
      (D-WLV-1) — never "0 = block by accident".
- [ ] **SVR-05** — Scope is **clean traffic only**. Whitelist/VIP-bypass traffic remains governed solely
      by the **VIP ceiling** (`vip_pps/vip_bps`); the fairness ladder (`ServicePlan`) is untouched. The
      three mechanisms stay orthogonal.
- [ ] **SVR-06** — Apply wire format bumps `APPLY_SNAPSHOT_SCHEMA_VERSION 2→3`: the service record
      carries `service_pps: le64`, `service_bps: le64`, `svc_rl_flags: u8`. The rule record stays
      **10 bytes** (already had no pps/bps). Readers reject an unknown schema version before touching maps.
- [ ] **SVR-07** — Alembic migration drops `allow_rule.pps`, `allow_rule.bps` and adds
      `protected_service.service_pps`, `protected_service.service_bps`. Existing per-rule pps/bps values
      are **discarded** (documented — they were unenforced no-ops).
- [ ] **SVR-08** — Data-plane: add slotted `svc_rl_config_map` (value `struct svc_rl_config
      {flags, pps, bps}`, keyed by `dp_id`, double-buffered) + unslotted per-CPU `svc_rl_state`
      (`rl_bucket`, keyed by `service_id`); remove the per-rule `rate_limit_state`, `rl_key`, `rl_config`,
      `rl_bucket_admit/consume/refill/reset`, and `rule_entry.pps/bps`. `rl_bucket` + `rl_burst` stay
      (shared with VIP). The loader pins the new maps and can seed them for standalone tests.
- [ ] **SVR-09** — Drop-reason ABI **unchanged**: `DR_RATE_LIMIT_DROP = 16→ (index 10)` keeps its index
      and name; it is now sourced from the service bucket instead of per-rule buckets. No enum
      renumber, no `DROP_REASON_COUNT` change.
- [ ] **SVR-10** — API/SPA: service create/update accepts `service_pps`/`service_bps` (validated like
      `vip_pps/vip_bps`); allow-rule create/update no longer accepts `pps`/`bps`. The change is
      **backward-incompatible** for API clients that sent rule pps/bps (documented in the migration note).
- [ ] **SVR-11** — Docs + telemetry: `apply_snapshot.h`, `data-plane/README.md`, PRD §6.4, the ARL spec
      (amendment), and the drop-reason label copy are updated so `rate_limit_drop` reads as
      service-level. Project memory `[[allow-rule-pps-bps-flags-blackhole]]` updated to "resolved by SVR".

## Non-Goals

| Excluded | Reason |
| --- | --- |
| Per-rule / per-port rate-limits | Explicitly removed. If ever needed, reintroduce behind a wire-format field, not flags-only. |
| Changing the VIP ceiling or the fairness ladder | Both stay as-is; SVR only occupies the clean-path allow→admit seam. |
| Per-source-IP rate-limits | PRD §13 / BL-08: no per-source state on the hot path. Buckets stay aggregate-per-service, per-CPU (rate÷nCPU, mirrors ARL/VIP). |
| A new drop-reason index | Reusing `DR_RATE_LIMIT_DROP` avoids an ABI append; the semantic ("matched-but-over-rate") is identical. |
| Migrating old rule pps/bps into a service value | Discarded (they were no-ops); operators re-enter one service-level value. |

## Gray Areas (resolved — see context.md)

- **D-SVR-1** identity of the single rate-limit → **new `service_pps`/`service_bps` on ProtectedService**
  (not a VIP-ceiling overload, not Plan-only). Keeps the three bandwidth mechanisms orthogonal.
- **D-SVR-2** dimensions → **both pps and bps** (each independently nullable), matching the removed
  per-rule knob's expressiveness.
- **D-SVR-3** scope → **clean (non-VIP) traffic only**; VIP keeps its own ceiling.

## Acceptance / Milestone Gate

- A service with `service_pps`/`service_bps` set: clean traffic within budget is `clean`, over budget is
  `rate_limit_drop` (verified by `BPF_PROG_TEST_RUN` deterministic bucket + a live veth smoke), while a
  whitelisted source on the same service is unaffected (still bound by VIP ceiling only).
- A service with both unset: **zero** `rate_limit_drop`, traffic bound only by the fairness ladder.
- `allow_rule` has no pps/bps columns; API rejects/ignores them; SPA rule form has no rate fields.
- `xdpgw-apply` accepts schema v3, rejects v2; structural read-back of `svc_rl_config_map` passes before
  the flip; `make test` (dp-unit) and `pytest -q` (cp) baselines advance with the added tests.
