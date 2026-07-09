# Whitelist/VIP (Scoped) & VIP Ceiling Context

**Gathered:** 2026-07-09
**Spec:** `.specs/features/whitelist-vip/spec.md` (approved as drafted, WLV-01..25)
**Status:** Ready for design (requires allow-rule-ratelimit executed first, A-WLV-8)

---

## Feature Boundary

Insert the §8.2 whitelist/VIP stage between the enabled-service hit and ARL's rule stage: bloom-guarded
scoped LPM match (key includes `service_id` — no cross-service bypass, BL-01/02), hit → aggregate
per-service VIP-ceiling token bucket → redirect (skipping the rule stage and, by position, every future
M3 #3 filter; never the 8.4 admit ladder), over-ceiling → terminal `vip_ceiling_drop` at frozen ABI
index 14, miss → rule stage untouched (post-ARL baseline passes unchanged). Slotted `whitelist_bloom`/
`whitelist_lpm` + slotted ceiling config = the M4 build contract; `vip_ceiling_state` unslotted runtime;
seed helper as interim writer; marked seams for M3 #3 (miss path) and M3 #4 (ingress cap before
whitelist). Out of scope: blacklists/amplification/bogon (#3), ingress cap + fairness ladder (#4), M4
worker build/swap, feed-overlap alert (M4), `expires_at` sweep (BL-07, GA), VIP-ceiling-hit alert (M6),
per-source VIP quotas (forbidden posture), whitelist dashboards (M5).

---

## Implementation Decisions

### NULL VIP ceiling = whitelist inactive (D-WLV-1)

- **A service whose `vip_pps` AND `vip_bps` are both NULL grants no bypass: the data-plane treats that
  service's whitelist as empty** — entries exist in config but are inert until the tenant/admin sets at
  least one ceiling dimension. Fail-safe reading of PRD §6.5 / BL-08's "VIP ceiling aggregate bắt buộc"
  (mandatory): an **uncapped bypass of every defence can never exist**, by construction.
- A dimension that is set governs; a dimension left NULL alongside a set one is **unlimited for that
  dimension only** (the mandatory-ceiling requirement is satisfied by the other dimension; mirrors
  ARL's per-dimension independence).
- **`0` = explicit block** (A-ARL-3 consistent): whitelisted traffic always drops `vip_ceiling_drop` on
  that dimension. Distinct from NULL.
- Consequence (accepted): a whitelist "silently does nothing" until a ceiling is set. Mitigation is
  control-plane UX, out of this feature's scope — captured as a deferred idea (SRL follow-up: warn on
  whitelist-entry create / entry-list read when both ceiling fields are NULL).
- Onboarding/docs language (WLV-25): "whitelist requires a VIP ceiling to take effect" + the BL-08
  residual failure mode (spoofed VIP flood → `vip_ceiling_drop` self-DoS, bounded by design).
- Realizes WLV-13. How "inactive" is enforced (builder refuses to emit entries for NULL/NULL services
  vs. kernel-side check) is a Design call within this decision — verdict must be identical either way
  (behave exactly as WLV-06 clean miss).

### Agent's Discretion

Design-time calls (fail-fast verification per project convention, not assumption):

- **Bloom granularity for CIDR entries** (A-WLV-1) — blooms test exact keys, not prefix containment;
  scheme (per-prefix-length insertion, fixed-prefix bucketing, …) must satisfy WLV-04's guard property
  (no false negatives, FP = cost only).
- **Map layout** (A-WLV-2) — shared LPM keyed `{service_id, prefix}` vs per-service inners; how
  `vip_pps`/`vip_bps` reach the kernel (extended `service_map` value vs separate slotted map). Result =
  M4 build contract.
- **Bloom feasibility de-risk** (A-WLV-3) — `BPF_MAP_TYPE_BLOOM_FILTER` (≥5.16) + slot composition
  proven at first load gate; documented fallback may degrade to LPM-only inside the same external
  contract (WLV-23).
- **VIP bucket mechanics** (A-WLV-5) — reuse AD-019 lazy version-reset + rate÷nCPU split + deterministic
  mode; key shape for `vip_ceiling_state`.
- **`pkt_meta` whitelist-outcome field** (A-WLV-7) — hit flag / stage verdict representation; struct may
  grow per convention if ARL left no pad.

---

## Specific References

- AD-003 / BL-01 / BL-02 (PRD §6.5, §12.3): scoped bypass keyed `service_id`+source; global maps never
  edited; whitelist A never bypasses B — the isolation invariant this feature implements.
- AD-005 / §8.3: whitelist maps config-slotted, `vip_ceiling_state` runtime-unslotted; swap resets the
  VIP bucket per D-ARL-2 precedent (WLV-15).
- AD-016/AD-017: `DR_VIP_CEILING_DROP = 14` pre-reserved in the frozen ABI; `record_drop` fuses count +
  sampling; `dpstat` decodes with zero changes (WLV-20).
- AD-019: lazy version-reset buckets, rate÷nCPU node bound, `test_no_refill` determinism — the VIP
  bucket reuses all three (WLV-15..17).
- PRD §8.4.6: VIP branch uses its own ceiling, never the committed/burst/node ladder (WLV-18).
- SRL model (SRL-22..25, SRL-01/06): `WhitelistEntry` keyed (`service_id`, `source_cidr`), arbitrary
  IPv4 source (D-SRL-1), IPv6 rejected; nullable BigInteger `vip_pps`/`vip_bps` on `ProtectedService` —
  the shapes the in-kernel contract mirrors.
- AD-002 / SRL-42: disabled service drop-all overrides whitelist (edge case, no data-plane work — stage
  is simply never reached).
- SRL-41: whitelist-before-blacklist precedence is pipeline-positional; M3 #3 inserts after the miss
  seam.
- D-SLRD-1: seed-helper-as-interim-writer posture reused for whitelist + ceiling (WLV-21).
- BL-07 (deferred): map holds only builder-active entries; expiry enforcement is the GA sweep (A-WLV-4).

---

## Deferred Ideas

- [ ] Control-plane warning/validation when whitelist entries exist on a service whose `vip_pps` and
      `vip_bps` are both NULL (whitelist inert per D-WLV-1) — SRL UX follow-up; also a UI banner on the
      whitelist view (GA candidate, pairs with the M6 VIP-ceiling-hit alert).
