# Blacklist (Bloom + LPM) & Deny Filters — Discuss Context

**Feature:** `.specs/features/blacklist-filters/` (BLK-01..26)
**Date:** 2026-07-09
**Spec status:** APPROVED as drafted (2026-07-09)
**Gray areas resolved:** D-BLK-1 (GA-1), D-BLK-2 (GA-2) — recorded in STATE.md as **AD-022**

---

## D-BLK-1: Hardcoded UDP amplification port set = full set including 53/123

**Decision (GA-1, option a):** the compile-time UDP source-port drop set is the **full reflection
set**: **17 (QOTD), 19 (chargen), 53 (DNS), 111 (portmap), 123 (NTP), 137 (NetBIOS), 161 (SNMP),
389 (CLDAP), 520 (RIP), 1900 (SSDP), 5353 (mDNS), 11211 (memcached)**.

**Why:** DNS (53) and NTP (123) are the two most common reflection/amplification vectors; excluding
them would gut the default posture the PRD's fail-fast intent implies. Most protected services
(web, game, API — inbound-only) never legitimately receive UDP *from* these source ports. The
escape hatch already exists and is principled: a tenant running a resolver/NTP client behind the
gateway whitelists its upstream servers (scoped, VIP-ceiling-capped per D-WLV-1) — the §6.5 VIP
exception covers the amplification filter by pipeline position (BLK-15).

**Consequences / how to apply:**
- BLK-11 ships the 12-port set as compile-time constants; changing it is a rebuild, not config.
- BLK-26 documents the set verbatim plus the onboarding guidance: *"a service that legitimately
  receives UDP from these source ports (e.g. a resolver's upstream DNS/NTP responses) must
  whitelist those upstream sources — whitelist requires an active VIP ceiling to take effect."*
- Novel/emerging reflection ports are the dynamic bitmap's job (D-BLK-2), not a reason to grow the
  hardcoded set mid-flight.

**Trade-off accepted:** resolver/NTP-style tenants have a mandatory whitelisting step at
onboarding; forgetting it means their upstream responses drop `udp_amplification_drop` (visible in
counters/dpstat, diagnosable).

---

## D-BLK-2: Dynamic blocked-port bitmap — seed-only writer in v1

**Decision (GA-2, option a):** v1 ships the `udp_blocked_port_bitmap` **enforcement + slotted map
contract** with the loader/seed helper as its **only writer** — the same D-SLRD-1 interim-writer
posture every config map started with. **No control-plane model, CRUD, or M4 job is created by
this feature.**

**Why:** PRD §7.1 deliberately has no blocked-port entity and SRL shipped no CRUD — inventing a
control-plane surface here would be speculative modeling for a map whose authoritative writer is
most naturally the GA auto-response (OP-02) or an M4 worker extension. The data-plane contract is
the durable part; the writer can land later without touching the hot path.

**Consequences / how to apply:**
- BLK-13/BLK-23: bitmap is demoable/testable via seed; default seed leaves it empty (BLK-16).
- The bitmap's operational story in the pilot: blocking a novel reflection port requires a seed
  write (operator action on the node), not an API call — documented in BLK-26.
- **Deferred idea recorded (STATE.md):** control-plane writer for the bitmap (minimal admin CRUD
  + `LIST_UPDATE`-style build path, or fold into GA auto-response OP-02).

**Trade-off accepted:** "dynamic" is operator-dynamic (no rebuild needed — a map write) but not
tenant/API-dynamic until a writer feature lands.

---

## Assumptions carried from the spec (flagged, not user decisions)

A-BLK-1..8 stand as drafted in `spec.md` — notably:

- **A-BLK-1:** bogon set = stable IANA special-purpose ranges **including RFC 5737 documentation
  ranges** → the dp-unit suite's RFC 5737 sources migrate deliberately (BLK-24); exact set
  confirmed at Design and documented verbatim.
- **A-BLK-2:** service blacklist reuses AD-021 machinery patterns; global blacklist = source-only
  keys; layouts = Design output = M4 build contract.
- **A-BLK-3:** `bloom_hit_lpm_miss` granularity (aggregate vs per-stage) + map home = Design;
  must stay outside `counter_map`, exact, and dpstat-visible.
- **A-BLK-5:** executes after WLV completes (seam B host; post-WLV baseline).
- **A-BLK-6:** 1M-entry envelope proven via map parameters + a gated/manual bulk-load check.

## Agent discretion at Design

- Global bloom key granularity for a 1M-entry set (bucket scheme, false-positive target, memory
  footprint documentation — BLK-08).
- Bitmap representation (8 KiB `ARRAY` of bits vs other), slot composition.
- `bloom_hit_lpm_miss` counter home + per-stage split; dpstat surface shape (BLK-19).
- FP-induction mechanism for tests (seed a bloom-only key vs test hook — BLK-17 independent test).
- Bogon check implementation (unrolled constant compares vs tiny LPM in rodata — must stay
  map-free per BLK-12).
- Suite-migration mechanics for RFC 5737 sources (BLK-24).

**Next phase:** Design.
