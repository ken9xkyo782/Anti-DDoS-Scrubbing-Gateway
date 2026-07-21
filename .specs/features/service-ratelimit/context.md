# Service-Level Rate-Limit — Discuss Context

Decisions captured via AskUserQuestion (2026-07-21). These bind spec.md and design.md.

## D-SVR-1 — What IS the single service rate-limit?

**Decision: a new dedicated field pair `service_pps`/`service_bps` on `ProtectedService`.**

Considered:
- **(chosen)** New `service_pps`/`service_bps` — one aggregate token bucket per service, enforced at the
  exact seam the per-rule bucket used to occupy (after allow-match, before the fairness ladder). VIP
  ceiling and `ServicePlan` stay separate and orthogonal.
- Reuse the VIP ceiling (`vip_pps/vip_bps`) and extend it to all traffic — rejected: it conflates
  "bypass-path ceiling" with "clean-path rate-limit"; VIP-bypass deliberately skips filtering and has its
  own semantics.
- Plan-only (`committed/ceiling_clean_gbps`) — rejected: Gbps-only, no pps dimension, and Plan is a
  fairness/SLA reservation, not an admission rate-limit.

**Why:** clearest mental model — three independent controls, each answering a different question:
`ServicePlan` = "guaranteed/critical bandwidth (fairness)", `vip_pps/vip_bps` = "cap on trusted bypass
traffic", `service_pps/service_bps` = "cap on normal allowed traffic". Mirrors the proven VIP
implementation so the data-plane risk is low.

## D-SVR-2 — Which dimensions?

**Decision: both `pps` and `bps`, each independently nullable.**

Keeps the expressiveness of the removed per-rule knob (which had both). A dimension left NULL is not
enforced; both NULL = no service rate-limit. Enforced with the VIP two-flag pattern
(`SVC_RL_F_PPS_SET`/`SVC_RL_F_BPS_SET`) so "0" is a real zero, never an accidental "unset = block".

## D-SVR-3 — Scope: which traffic does it cap?

**Decision: clean (non-VIP) allowed traffic only.**

Whitelist/VIP-bypass traffic stays governed solely by the VIP ceiling; the fairness ladder is unchanged.
The service rate-limit is checked on the `admit_clean` path only. This preserves the §8.2 branch
semantics: a whitelist hit → VIP ceiling → redirect; a miss → rule match → **service rate-limit** →
fairness ladder → redirect.

## Assumptions (A-SVR)

- **A-SVR-1** Reusing `DR_RATE_LIMIT_DROP` (index 10) is acceptable — same operator-facing meaning
  ("allowed but over rate"); avoids a drop-reason ABI append. Telemetry copy relabels it service-level.
- **A-SVR-2** Existing per-rule pps/bps in the DB are no-ops today; discarding them on migrate loses no
  live behavior. Operators re-enter one service value if desired.
- **A-SVR-3** The apply wire-format bump (v2→v3) is coordinated: `xdpgw-apply` (reader) and `applier.py`
  (writer) ship together; the loader/worker are redeployed together (single-node pilot, no rolling mix).
- **A-SVR-4** Rate÷nCPU aggregate-per-CPU accounting (from ARL/VIP, AD-019) is the accepted precision
  trade-off; node-level deviation is bounded and documented — no per-source-IP state.
- **A-SVR-5** `rl_bucket` struct + `rl_burst()` remain shared with VIP; only the per-rule-specific bucket
  machinery is deleted.
